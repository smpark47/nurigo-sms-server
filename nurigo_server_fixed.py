# -*- coding: utf-8 -*-
"""
Nurigo/Solapi SMS proxy (Flask) with Send Logs

Endpoints
  GET  /                    -> health
  GET  /routes              -> list routes (debug)
  GET  /api/sms/config      -> {"provider": "...", "defaultFrom": "010..."}
  POST /api/sms             -> {to, from, text, teacher?, student?, dry?}
  GET  /api/sms/logs        -> recent logs (JSON, ?limit=50)
  GET  /api/sms/logs.csv    -> recent logs (CSV)
  GET  /ui                  -> simple web UI

Env Vars
  PORT            : bind port (Render sets this automatically)
  DEFAULT_SENDER  : default "from" number (e.g., 01080348069)
  SOLAPI_KEY      : Solapi API key (use if not forwarding)
  SOLAPI_SECRET   : Solapi API secret
  FORWARD_URL     : if set, forward JSON to this URL instead of calling Solapi
  AUTH_TOKEN      : if set, require header "Authorization: Bearer <AUTH_TOKEN>"
  LOG_PATH        : logs file path (default: sms_logs.jsonl)
  LOG_MAX         : in-memory recent logs count (default: 5000)
"""
import os, json, hmac, hashlib, secrets, requests
from datetime import datetime, timezone
from collections import deque
from threading import Lock

from flask import Flask, request, jsonify, Response
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ---- Config ----
DEFAULT_SENDER = os.getenv("DEFAULT_SENDER", "").strip()
FORWARD_URL    = os.getenv("FORWARD_URL", "").strip()
SOLAPI_KEY     = os.getenv("SOLAPI_KEY", "").strip()
SOLAPI_SECRET  = os.getenv("SOLAPI_SECRET", "").strip()
AUTH_TOKEN     = os.getenv("AUTH_TOKEN", "").strip()

LOG_PATH = os.getenv("LOG_PATH", "sms_logs.jsonl")
LOG_MAX  = int(os.getenv("LOG_MAX", "5000"))

# ---- In-memory Logs + File Append ----
_LOG_Q: deque = deque(maxlen=LOG_MAX)
_LOG_LOCK = Lock()

def current_provider() -> str:
    if FORWARD_URL:
        return "forward"
    if SOLAPI_KEY and SOLAPI_SECRET:
        return "solapi"
    return "mock"

def _append_log(rec: dict):
    """Append a record both to memory and JSONL file. Non-fatal on file errors."""
    rec = dict(rec)
    with _LOG_LOCK:
        _LOG_Q.append(rec)
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass

def _utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

# ---- Routes ----
@app.get("/")
def root():
    return {"ok": True, "service": "nurigo-sms-proxy", "provider": current_provider()}, 200

@app.get("/routes")
def routes():
    return {"routes": [{"rule": r.rule, "methods": sorted(list(r.methods))} for r in app.url_map.iter_rules()]}

@app.get("/api/sms/config")
def sms_config():
    return jsonify({"provider": current_provider(), "defaultFrom": DEFAULT_SENDER})

def check_auth():
    if not AUTH_TOKEN:
        return True, None
    got = request.headers.get("Authorization", "")
    if got.startswith("Bearer "):
        token = got.split(" ", 1)[1].strip()
        if token == AUTH_TOKEN:
            return True, None
    return False, (jsonify({"ok": False, "error": "unauthorized"}), 401)

@app.post("/api/sms")
def sms_send():
    ok, err = check_auth()
    if not ok:
        return err

    try:
        payload = request.get_json(force=True) or {}
    except Exception:
        payload = {}

    to       = str(payload.get("to", "")).strip()
    from_num = str(payload.get("from", DEFAULT_SENDER)).strip() or DEFAULT_SENDER
    text     = str(payload.get("text", "")).strip()
    dry      = bool(payload.get("dry", False))
    teacher  = str(payload.get("teacher", "")).strip()
    student  = str(payload.get("student", "")).strip()

    if not to or not text:
        return jsonify({"ok": False, "error": "missing to/text"}), 400

    # Dry run: no forwarding / no external API
    if dry:
        now = _utc_now()
        out = {
            "ok": True, "provider": "mock", "dry": True,
            "echo": {"to": to, "from": from_num, "text": text, "len": len(text)},
            "at": now,
        }
        _append_log({
            "at": now, "teacher": teacher, "student": student,
            "to": to, "from": from_num, "text": text, "len": len(text),
            "dry": True, "provider": "mock", "ok": True, "status": 200
        })
        return jsonify(out)

    # Forwarding
    if FORWARD_URL:
        try:
            r = requests.post(
                FORWARD_URL,
                json={"to": to, "from": from_num, "text": text, "teacher": teacher, "student": student},
                timeout=15,
            )
            now = _utc_now()
            _append_log({
                "at": now, "teacher": teacher, "student": student,
                "to": to, "from": from_num, "text": text, "len": len(text),
                "dry": False, "provider": "forward", "ok": r.status_code < 300, "status": r.status_code
            })
            return (
                r.text,
                r.status_code,
                {"Content-Type": r.headers.get("Content-Type", "application/json")},
            )
        except Exception as e:
            return jsonify({"ok": False, "error": "forward-failed", "detail": str(e)}), 502

    # Direct Solapi call (HMAC-SHA256)
    if SOLAPI_KEY and SOLAPI_SECRET:
        try:
            date_time = _utc_now()
            salt = secrets.token_hex(16)
            signature = hmac.new(
                SOLAPI_SECRET.encode("utf-8"),
                (date_time + salt).encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()

            auth_header = (
                f"HMAC-SHA256 apiKey={SOLAPI_KEY}, date={date_time}, "
                f"salt={salt}, signature={signature}"
            )

            r = requests.post(
                "https://api.solapi.com/messages/v4/send",
                headers={"Content-Type": "application/json", "Authorization": auth_header},
                json={"message": {"to": to, "from": from_num, "text": text}},
                timeout=15,
            )
            ctype = r.headers.get("Content-Type", "")
            data = r.json() if ctype and "application/json" in ctype.lower() else {"raw": r.text}
            out = {"ok": r.status_code < 300, "provider": "solapi", "response": data}

            now = _utc_now()
            _append_log({
                "at": now, "teacher": teacher, "student": student,
                "to": to, "from": from_num, "text": text, "len": len(text),
                "dry": False, "provider": "solapi", "ok": r.status_code < 300, "status": r.status_code
            })
            return (json.dumps(out, ensure_ascii=False), r.status_code, {"Content-Type": "application/json"})
        except Exception as e:
            return jsonify({"ok": False, "error": "solapi-failed", "detail": str(e)}), 502

    # Fallback mock if no forwarding/solapi configured
    now = _utc_now()
    _append_log({
        "at": now, "teacher": teacher, "student": student,
        "to": to, "from": from_num, "text": text, "len": len(text),
        "dry": True, "provider": "mock", "ok": True, "status": 200
    })
    return jsonify({
        "ok": True, "provider": "mock", "dry": True,
        "echo": {"to": to, "from": from_num, "text": text, "len": len(text)},
        "at": now,
    })

@app.get("/api/sms/logs")
def sms_logs():
    """Recent logs (JSON). Use ?limit=100 (default 50)."""
    ok, err = check_auth()
    if not ok: return err
    try:
        limit = int(request.args.get("limit", "50"))
    except Exception:
        limit = 50
    with _LOG_LOCK:
        data = list(_LOG_Q)[-limit:]
    return jsonify({"ok": True, "logs": data, "count": len(data)})

@app.get("/api/sms/logs.csv")
def sms_logs_csv():
    """Download logs as CSV."""
    ok, err = check_auth()
    if not ok: return err
    import csv, io
    with _LOG_LOCK:
        rows = list(_LOG_Q)
    cols = ["at","teacher","student","to","from","text","len","dry","provider","ok","status"]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in cols})
    return Response(buf.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition":"attachment; filename=logs.csv"})

# --- Simple Web UI ---
WEB_UI_HTML = r"""<!doctype html>
<html lang="ko"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>문자 전송 프로그램</title>
<link rel="icon" href='data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect width="100" height="100" rx="22" fill="%232563eb"/><text x="50" y="62" text-anchor="middle" font-size="60" fill="white">💬</text></svg>' type="image/svg+xml">
<meta name="theme-color" content="#2563eb">
<style>
:root{--b:#cbd5e1;--text:#334155;--muted:#64748b;--bg:#f8fafc;--white:#fff;--brand:#2563eb;--accent:#0ea5e9}
*{box-sizing:border-box}
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Apple SD Gothic Neo,Noto Sans KR,Arial,sans-serif;background:var(--bg);margin:0}
.wrap{max-width:980px;margin:24px auto;padding:16px}
.card{background:var(--white);border:1px solid #e5e7eb;border-radius:12px;padding:16px;box-shadow:0 1px 2px rgba(0,0,0,.04)}
.row{display:flex;gap:12px;flex-wrap:wrap;align-items:flex-start}
.controls{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(220px,1fr))}
.col{display:flex;flex-direction:column;gap:6px;min-width:220px}
label{display:block;font-size:12px;color:var(--text)}
input,select,textarea{width:100%;padding:10px 12px;border:1px solid var(--b);border-radius:10px;font-size:14px;background:var(--white)}
textarea{min-height:120px}
button{padding:10px 14px;border-radius:10px;border:1px solid var(--b);background:var(--white);cursor:pointer}
button.primary{background:var(--brand);color:var(--white);border-color:var(--brand)}
.pill{padding:8px 12px;border-radius:999px;border:1px solid var(--b);background:var(--white);font-size:13px;cursor:pointer;white-space:nowrap}
.pill.on{background:var(--accent);color:var(--white);border-color:var(--accent)}
.muted{color:var(--muted);font-size:12px}
.grid{display:grid;gap:10px}
.grid.teachers{grid-template-columns:repeat(auto-fill,minmax(120px,1fr))}
.grid.students{grid-template-columns:repeat(auto-fill,minmax(120px,1fr))}
.templates{display:flex;flex-wrap:wrap;gap:8px}
.mt8{margin-top:8px}.mt12{margin-top:12px}.mt16{margin-top:16px}.mt24{margin-top:24px}
pre{background:#0b1020;color:#c7d2fe;padding:12px;border-radius:10px;overflow:auto}
h3{margin:0 0 8px 0;font-size:16px}

/* send-row layout */
.actionbar{display:flex;align-items:center;gap:10px;flex-wrap:wrap}

/* Safari gap issue: remove gap inside inlinecheck and use precise margin */
.inlinecheck{
  display:inline-flex;
  align-items:center;
  white-space:nowrap;   /* keep "dry-run" on one line */
  line-height:1.1;
}
.inlinecheck input{
  margin:0;             /* reset Safari default spacing */
  appearance:auto;
  -webkit-appearance:checkbox;
  width:16px;height:16px;
  vertical-align:middle;
}
.inlinecheck span{
  display:inline-block;
  margin-left:4px;      /* exact spacing between checkbox and label */
}

/* status text doesn't overlap; responsive placement */
.status{
  margin-left:auto;
  white-space:nowrap;   /* desktop keep one line */
}
@media (max-width:600px){
  .status{
    order:3;
    flex-basis:100%;    /* force to next line on small screens */
    margin-left:0;
    white-space:normal; /* allow wrap on mobile */
  }
  #send{ order:1; }
  .inlinecheck{ order:2; }
}

/* mobile safety */
#search{max-width:100%}
.table{width:100%;border-collapse:collapse}
.table th,.table td{padding:6px 4px;border-bottom:1px solid #f1f5f9;text-align:left;font-size:13px}
.table th{border-bottom:1px solid #e5e7eb;color:#334155}
</style>
</head>
<body>
<div class="wrap">
  <h2>문자 전송 프로그램</h2>

  <div class="card">
    <div class="controls">
      <div class="col">
        <label>발신번호 (서버 기본값)</label>
        <input id="fromNum" disabled>
        <div id="cfgInfo" class="muted mt8">서버 설정을 불러오는 중...</div>
      </div>
      <div class="col">
        <label>검색(학생)</label>
        <input id="search" placeholder="이름 일부로 필터링">
      </div>
    </div>
  </div>

  <div class="card mt16">
    <h3>1) 선생님 → 담당학생 선택</h3>
    <div class="mt8">
      <label>선생님</label>
      <div id="teacherBox" class="grid teachers"></div>
    </div>
    <div class="mt12">
      <label>담당 학생</label>
      <div id="studentBox" class="grid students"></div>
      <div class="muted mt8">학생 버튼 클릭 시 수신번호가 자동 선택됩니다.</div>
    </div>
  </div>

  <div class="card mt16">
    <h3>2) 문구 선택 → 발송</h3>
    <div class="row">
      <div class="col">
        <label>수신 대상</label>
        <div class="templates">
          <span class="pill on" data-to="parent">학부모</span>
          <span class="pill" data-to="student">학생</span>
          <span class="pill" data-to="custom">직접</span>
          <input id="customTo" placeholder="직접 입력 (예: 01012345678)" style="display:none;flex:1 1 240px">
        </div>
        <div class="muted mt8">현재 수신번호: <b id="toPreview">-</b></div>
      </div>
      <div class="col">
        <label>원클릭 문구</label>
        <div class="templates" id="tpls"></div>
      </div>
    </div>

    <div class="mt12">
      <label>문자 내용</label>
      <textarea id="text" placeholder="{given} 자리는 (성 빼고) 이름으로 치환됩니다."></textarea>
      <div class="muted mt8">미리보기: <span id="preview"></span></div>
    </div>

    <div class="actionbar mt16">
      <button id="send" class="primary">전송</button>
      <label for="dry" class="inlinecheck">
        <input type="checkbox" id="dry" />
        <span class="muted">dry-run</span>
      </label>
      <span id="status" class="muted status"></span>
    </div>

    <div class="mt12">
      <label>결과</label>
      <pre id="out">(아직 없음)</pre>
    </div>
  </div>

  <div class="card mt16">
    <h3>3) 발송 로그</h3>
    <div class="row">
      <button id="refreshLogs">새로고침</button>
      <a href="/api/sms/logs.csv" class="pill">CSV 다운로드</a>
    </div>
    <div class="mt12">
      <table id="logTable" class="table">
        <thead>
          <tr>
            <th>시간</th>
            <th>선생님</th>
            <th>학생</th>
            <th>수신</th>
            <th>내용(앞부분)</th>
            <th>상태</th>
          </tr>
        </thead>
        <tbody></tbody>
      </table>
      <div class="muted mt8">최근 50건 표시</div>
    </div>
  </div>
</div>

<script>
// ===== ROSTER (샘플) =====
// 실제 명단으로 교체하세요. 필요 시 CSV→자동생성 버전으로 바꿔드릴 수 있습니다.
// '박선민', '주말반쌤'은 제외 요구에 맞게 사용 시 해당 키를 넣지 마세요.
const ROSTER = {
  "예시선생님": [
    { id:"예시선생님::홍길동", name:"홍길동", parentPhone:"01012345678", studentPhone:"" },
    { id:"예시선생님::김철수", name:"김철수", parentPhone:"01011112222", studentPhone:"" }
  ]
};
// ========================

function givenName(full) {
  const s = String(full||"").trim();
  if (!s) return "";
  if (/^[가-힣]+$/.test(s) && s.length >= 2) return s.slice(1);
  const parts = s.split(/\s+/);
  return parts.length > 1 ? parts[parts.length-1] : s;
}

const TEMPLATES = [
  { label:"미등원 안내",  text:"안녕하세요. 서울더함수학학원입니다. {given} 아직 등원 하지 않았습니다." },
  { label:"조퇴 안내",   text:"서울더함수학학원입니다. {given} 아파서 오늘 조퇴하였습니다. 아이 상태 확인해주세요." },
  { label:"숙제 미체출",  text:"서울더함수학학원입니다. {given} 오늘 과제 미체출입니다. 가정에서 점검 부탁드립니다." },
  { label:"교재 공지",   text:"안녕하세요. 서울더함수학학원입니다. {given} 새로운 교재 준비 부탁드립니다." }
];

const onlyDigits = s => (s||"").replace(/\D/g,"");
const norm = s => {
  const d=onlyDigits(s);
  if (d.length===11) return d.replace(/(\d{3})(\d{4})(\d{4})/,"$1-$2-$3");
  if (d.length===10) return d.replace(/(\d{2,3})(\d{3,4})(\d{4})/,"$1-$2-$3");
  return s||"";
};
const $  = sel => document.querySelector(sel);
const $$ = sel => Array.from(document.querySelectorAll(sel));

const state = {
  roster: ROSTER,
  teacherList: Object.keys(ROSTER),
  currentTeacher: Object.keys(ROSTER)[0] || "",
  currentStudent: null,
  toType: "parent",
  defaultFrom: ""
};

async function loadConfig(){
  try{
    const r=await fetch("/api/sms/config");
    if(!r.ok) throw new Error("bad config");
    const cfg=await r.json();
    state.defaultFrom=String(cfg.defaultFrom||"");
    $("#fromNum").value=state.defaultFrom||"(서버 미설정)";
    $("#cfgInfo").textContent="provider: "+(cfg.provider||"unknown");
  }catch(e){ $("#cfgInfo").textContent="서버 설정을 불러오지 못했습니다."; }
}

function setupTemplates(){
  const box=$("#tpls"); box.innerHTML="";
  TEMPLATES.forEach(t=>{
    const b=document.createElement("button");
    b.className="pill";
    b.textContent=t.label;
    b.addEventListener("click",()=>{
      const s = state.currentStudent;
      const txt = t.text.replaceAll("{given}", givenName(s?.name||""));
      $("#text").value = txt;
      updatePreview();
    });
    box.appendChild(b);
  });
}

function setupToType(){
  $$(".pill[data-to]").forEach(p=>{
    p.addEventListener("click",()=>{
      $$(".pill[data-to]").forEach(x=>x.classList.remove("on"));
      p.classList.add("on");
      state.toType = p.dataset.to;
      $("#customTo").style.display = (state.toType==="custom") ? "block" : "none";
      updatePreview();
    });
  });
  $("#customTo").addEventListener("input", updatePreview);
}

function renderTeachers(){
  const box=$("#teacherBox"); box.innerHTML="";
  if(!state.teacherList.length){
    box.innerHTML='<span class="muted">선생님 데이터가 없습니다. ROSTER를 채워주세요.</span>'; return;
  }
  state.teacherList.forEach(t=>{
    const b=document.createElement("button");
    b.className="pill"+(t===state.currentTeacher?" on":"");
    b.textContent = t;
    b.addEventListener("click",()=>{
      state.currentTeacher=t;
      state.currentStudent=null;
      renderTeachers(); renderStudents(); updatePreview();
    });
    box.appendChild(b);
  });
}

function renderStudents(){
  const box=$("#studentBox"); box.innerHTML="";
  const list = (state.roster[state.currentTeacher]||[]);
  const q = ($("#search").value||"").trim();
  const filtered = q ? list.filter(s=>s.name && s.name.includes(q)) : list;

  if(!filtered.length){
    box.innerHTML='<span class="muted">학생이 없습니다.</span>';
    state.currentStudent=null; updatePreview(); return;
  }
  filtered.forEach(s=>{
    const b=document.createElement("button");
    b.className="pill"+(state.currentStudent && state.currentStudent.id===s.id ? " on":"");
    b.textContent = s.name;
    b.addEventListener("click",()=>{
      state.currentStudent=s;
      if(!$("#text").value.trim()){
        const t=TEMPLATES[0];
        $("#text").value = t.text.replaceAll("{given}", givenName(s.name||""));
      }
      updatePreview(); renderStudents();
    });
    box.appendChild(b);
  });
}

function computeTo(){
  if(state.toType==="custom") return norm($("#customTo").value||"");
  const s=state.currentStudent; if(!s) return "";
  if(state.toType==="parent")  return norm(s.parentPhone||"");
  if(state.toType==="student") return norm(s.studentPhone||"");
  return "";
}

function updatePreview(){
  const s = state.currentStudent;
  $("#toPreview").textContent = computeTo() || "-";
  const txt=$("#text").value||"";
  $("#preview").textContent = txt.replaceAll("{given}", givenName(s?.name||""));
}

async function send(){
  const s=state.currentStudent;
  const to=onlyDigits(computeTo());
  const from=onlyDigits(state.defaultFrom||"");
  const dry=$("#dry").checked;
  const text=($("#text").value||"").replaceAll("{given}", givenName(s?.name||""));

  $("#status").textContent="전송 중...";
  if(!s){ alert("학생을 먼저 선택하세요."); $("#status").textContent=""; return; }
  if(!to){ alert("수신 번호가 비어있습니다."); $("#status").textContent=""; return; }
  if(!text.trim()){ alert("문자 내용을 입력하세요."); $("#status").textContent=""; return; }

  const payload={to,from,text,student:s.name,teacher:state.currentTeacher,dry};
  try{
    const r=await fetch("/api/sms",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    const data=await r.json().catch(()=>({ok:false,status:r.status}));
    $("#out").textContent=JSON.stringify(data,null,2);
    $("#status").textContent=r.ok?(dry?"드라이런 완료":"전송 요청 완료"):"전송 실패";
  }catch(e){
    $("#out").textContent=String(e);
    $("#status").textContent="오류";
  }
  await loadLogs(); // 전송 후 로그 갱신
}

function renderLogs(items){
  const tb = document.querySelector("#logTable tbody");
  tb.innerHTML = items.map(r=>{
    const title = (r.text||"").slice(0,30).replace(/\n/g," ");
    const ok = r.ok ? "✅" : "❌";
    const dry = r.dry ? "DRY" : "REAL";
    return `<tr>
      <td>${r.at||""}</td>
      <td>${r.teacher||""}</td>
      <td>${r.student||""}</td>
      <td>${r.to||""}</td>
      <td>${title}</td>
      <td>${ok} / ${dry} / ${r.provider||""}</td>
    </tr>`;
  }).join("");
}

async function loadLogs(){
  try{
    const r = await fetch("/api/sms/logs?limit=50");
    const data = await r.json();
    if(data.ok){ renderLogs(data.logs||[]); }
  }catch(e){ /* ignore */ }
}

// init
(async function(){
  await loadConfig();
  setupTemplates();
  setupToType();

  // teacher list init
  state.teacherList = Object.keys(state.roster);
  state.currentTeacher = state.teacherList[0] || "";
  renderTeachers(); renderStudents(); updatePreview();

  $("#search").addEventListener("input", renderStudents);
  $("#text").addEventListener("input", updatePreview);
  $("#send").addEventListener("click", send);
  document.getElementById("refreshLogs").addEventListener("click", loadLogs);

  await loadLogs();
})();
</script>
</body></html>
"""

@app.get("/ui")
def ui():
    return Response(WEB_UI_HTML, mimetype="text/html; charset=utf-8")

# (참고) favicon 전용 라우트가 204를 반환하면 <link rel="icon">가 무시될 수 있습니다.
# 현재는 <head>에 data URL 파비콘을 넣었으니 별도 라우트가 없어도 동작합니다.

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    print("== URL MAP ==")
    print(app.url_map)
    app.run(host="0.0.0.0", port=port, debug=False)
