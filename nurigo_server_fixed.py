# -*- coding: utf-8 -*-
"""
Nurigo/Solapi SMS proxy (Flask)

Endpoints
  GET  /                   -> health
  GET  /routes             -> list routes (debug)
  GET  /api/sms/config     -> {"provider": "...", "defaultFrom": "010..."}
  POST /api/sms            -> {to, from, text, dry?}
  GET  /api/roster         -> {ok, teachers, roster}  # CSV/JSON에서 담당 매핑 자동 로드
  GET  /ui                 -> simple web UI

Env Vars
  PORT             : bind port (Render sets this automatically)
  DEFAULT_SENDER   : default "from" number (e.g., 01080348069)
  SOLAPI_KEY       : Solapi API key (use if not forwarding)
  SOLAPI_SECRET    : Solapi API secret
  FORWARD_URL      : if set, forward JSON to this URL instead of calling Solapi
  AUTH_TOKEN       : if set, require header "Authorization: Bearer <AUTH_TOKEN>"

  # Roster auto-loading (아래 우선순위대로 사용)
  ROSTER_JSON      : JSON 문자열 ({"teachers":[...], "roster":{teacher:[...]}})
  ROSTER_CSV       : CSV 전체 텍스트
  ROSTER_URL       : CSV/JSON의 공개 URL (예: https://.../static/roster.csv)
  static/roster.csv: 리포 내 정적 파일이 존재하면 이를 사용
"""
import os, io, json, hmac, hashlib, secrets, requests, csv
from datetime import datetime, timezone

from flask import Flask, request, jsonify, Response, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder="static")
CORS(app)  # tighten allowed origins in production

DEFAULT_SENDER = os.getenv("DEFAULT_SENDER", "").strip()
FORWARD_URL    = os.getenv("FORWARD_URL", "").strip()
SOLAPI_KEY     = os.getenv("SOLAPI_KEY", "").strip()
SOLAPI_SECRET  = os.getenv("SOLAPI_SECRET", "").strip()
AUTH_TOKEN     = os.getenv("AUTH_TOKEN", "").strip()

ROSTER_JSON    = os.getenv("ROSTER_JSON", "")
ROSTER_CSV     = os.getenv("ROSTER_CSV", "")
ROSTER_URL     = os.getenv("ROSTER_URL", "").strip()

# --- helpers -----------------------------------------------------------------

def current_provider() -> str:
    if FORWARD_URL:
        return "forward"
    if SOLAPI_KEY and SOLAPI_SECRET:
        return "solapi"
    return "mock"

def _keyify(s: str) -> str:
    return "".join(ch for ch in s.strip().lower() if ch.isalnum() or '\uac00' <= ch <= '\ud7a3')

TEACHER_KEYS = {"담당","담당선생","담당선생님","선생님","teacher","tch","담당자"}
NAME_KEYS    = {"학생이름","이름","name","student","학생","성명"}
PARENT_KEYS  = {"학부모전화","학부모연락처","부모전화","보호자전화","보호자연락처","parent","parentphone"}
STUDENT_KEYS = {"학생전화","연락처","student","studentphone","전화번호","핸드폰","휴대폰","mobile","cell"}

def _detect_indices(headers):
    H = [_keyify(h) for h in headers]
    def find(cands):
        for i, h in enumerate(H):
            if h in cands:
                return i
        return -1
    idx = {
        "teacher": find({ _keyify(x) for x in TEACHER_KEYS }),
        "name":    find({ _keyify(x) for x in NAME_KEYS }),
        "parent":  find({ _keyify(x) for x in PARENT_KEYS }),
        "student": find({ _keyify(x) for x in STUDENT_KEYS }),
    }
    return idx

def _only_digits(s): return "".join(ch for ch in (s or "") if ch.isdigit())

def _build_roster_from_csv_text(text: str):
    # 파서: utf-8-sig 안전, 따옴표/콤마 처리
    f = io.StringIO(text.replace("\r\n","\n").replace("\r","\n"))
    reader = csv.reader(f)
    rows = list(reader)
    if not rows:
        return {"ok": False, "error": "empty-csv"}

    headers = rows[0]
    idx = _detect_indices(headers)
    if idx["teacher"] < 0 or idx["name"] < 0:
        return {"ok": False, "error": "header-missing"}

    roster = {}
    teachers_set = set()
    for r in rows[1:]:
        if not r or len(r) < 2: 
            continue
        teacher = (r[idx["teacher"]] if idx["teacher"]>=0 and idx["teacher"]<len(r) else "").strip()
        name    = (r[idx["name"]]    if idx["name"]>=0    and idx["name"]<len(r) else "").strip()
        if not teacher or not name:
            continue
        parent  = _only_digits(r[idx["parent"]])  if idx["parent"] >=0 and idx["parent"] < len(r) else ""
        student = _only_digits(r[idx["student"]]) if idx["student"]>=0 and idx["student"]<len(r) else ""
        obj = {"id": f"{teacher}::{name}", "name": name, "parentPhone": parent, "studentPhone": student}
        roster.setdefault(teacher, []).append(obj)
        teachers_set.add(teacher)

    teachers = sorted(teachers_set, key=lambda x: x)
    for t in teachers:
        roster[t].sort(key=lambda s: s["name"])
    return {"ok": True, "teachers": teachers, "roster": roster}

def _load_roster():
    # 1) JSON env
    if ROSTER_JSON:
        try:
            data = json.loads(ROSTER_JSON)
            if "teachers" in data and "roster" in data:
                return {"ok": True, "teachers": data["teachers"], "roster": data["roster"], "source": "env-json"}
        except Exception:
            pass

    # 2) CSV env
    if ROSTER_CSV:
        out = _build_roster_from_csv_text(ROSTER_CSV)
        if out.get("ok"):
            out["source"] = "env-csv"
            return out

    # 3) URL (CSV or JSON)
    if ROSTER_URL:
        try:
            r = requests.get(ROSTER_URL, timeout=15)
            ctype = (r.headers.get("Content-Type","") or "").lower()
            txt = r.text
            if "json" in ctype:
                data = r.json()
                if "teachers" in data and "roster" in data:
                    return {"ok": True, "teachers": data["teachers"], "roster": data["roster"], "source": "url-json"}
            # assume CSV otherwise
            # strip UTF-8 BOM if any
            if txt and txt[:1] == "\ufeff":
                txt = txt[1:]
            out = _build_roster_from_csv_text(txt)
            if out.get("ok"):
                out["source"] = "url-csv"
                return out
        except Exception as e:
            return {"ok": False, "error": f"url-fetch-failed: {e}"}

    # 4) static/roster.csv in repo
    local_static = os.path.join(app.static_folder, "roster.csv")
    if os.path.exists(local_static):
        try:
            with open(local_static, "r", encoding="utf-8-sig") as f:
                txt = f.read()
            out = _build_roster_from_csv_text(txt)
            if out.get("ok"):
                out["source"] = "static-csv"
                return out
        except Exception as e:
            return {"ok": False, "error": f"static-read-failed: {e}"}

    return {"ok": False, "error": "roster-not-configured"}

# --- routes ------------------------------------------------------------------

@app.get("/")
def root():
    return {"ok": True, "service": "nurigo-sms-proxy", "provider": current_provider()}, 200

@app.get("/routes")
def routes():
    return {"routes": [{"rule": r.rule, "methods": sorted(list(r.methods))} for r in app.url_map.iter_rules()]}

@app.get("/api/sms/config")
def sms_config():
    return jsonify({"provider": current_provider(), "defaultFrom": DEFAULT_SENDER})

@app.get("/api/roster")
def api_roster():
    data = _load_roster()
    status = 200 if data.get("ok") else 404
    return jsonify(data), status

def check_auth():
    # Optional bearer gate to prevent open relay
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

    if not to or not text:
        return jsonify({"ok": False, "error": "missing to/text"}), 400

    # DRY-RUN: never forward or call Solapi when dry=True
    if dry:
        return jsonify({
            "ok": True,
            "provider": "mock",
            "dry": True,
            "echo": {"to": to, "from": from_num, "text": text, "len": len(text)},
            "at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        })

    # 1) Forwarding to existing HTTP SMS service
    if FORWARD_URL:
        try:
            r = requests.post(
                FORWARD_URL,
                json={"to": to, "from": from_num, "text": text},
                timeout=15,
            )
            return (
                r.text,
                r.status_code,
                {"Content-Type": r.headers.get("Content-Type", "application/json")},
            )
        except Exception as e:
            return jsonify({"ok": False, "error": "forward-failed", "detail": str(e)}), 502

    # 2) Direct call to Solapi (HMAC-SHA256)
    if SOLAPI_KEY and SOLAPI_SECRET:
        try:
            # Authorization: HMAC-SHA256 apiKey=<key>, date=<ISO8601Z>, salt=<hex>, signature=<hex>
            date_time = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            salt = secrets.token_hex(16)  # random per request
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
            return (json.dumps(out, ensure_ascii=False), r.status_code, {"Content-Type": "application/json"})
        except Exception as e:
            return jsonify({"ok": False, "error": "solapi-failed", "detail": str(e)}), 502

    # 3) Fallback mock when neither forwarding nor solapi creds are present
    return jsonify({
        "ok": True,
        "provider": "mock",
        "dry": True,
        "echo": {"to": to, "from": from_num, "text": text, "len": len(text)},
        "at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    })

# --- Simple Web UI (same origin) ---
WEB_UI_HTML = r"""<!doctype html>
<html lang="ko"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SMS 웹 발송 · 선생님/담당학생 자동 로딩</title>
<style>
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Apple SD Gothic Neo,Noto Sans KR,Arial,sans-serif;background:#f8fafc;margin:0}
.wrap{max-width:980px;margin:24px auto;padding:16px}
.card{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:16px;box-shadow:0 1px 2px rgba(0,0,0,.04)}
.row{display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end}
.col{flex:1 1 260px;min-width:260px}
label{display:block;font-size:12px;color:#334155;margin-bottom:6px}
input,select,textarea{width:100%;padding:10px 12px;border:1px solid #cbd5e1;border-radius:10px;font-size:14px}
textarea{min-height:120px}
button{padding:10px 14px;border-radius:10px;border:1px solid #cbd5e1;background:#fff;cursor:pointer}
button.primary{background:#2563eb;color:#fff;border-color:#2563eb}
.pill{padding:8px 12px;border-radius:999px;border:1px solid #cbd5e1;background:#fff;font-size:13px;cursor:pointer;white-space:nowrap}
.pill.on{background:#0ea5e9;color:#fff;border-color:#0ea5e9}
.muted{color:#64748b;font-size:12px}
.grid{display:grid;gap:10px}
.grid.teachers{grid-template-columns:repeat(auto-fill,minmax(120px,1fr))}
.grid.students{grid-template-columns:repeat(auto-fill,minmax(120px,1fr))}
.templates{display:flex;flex-wrap:wrap;gap:8px}
.mt8{margin-top:8px}.mt12{margin-top:12px}.mt16{margin-top:16px}.mt24{margin-top:24px}
pre{background:#0b1020;color:#c7d2fe;padding:12px;border-radius:10px;overflow:auto}
.badge{font-size:11px;background:#eef2ff;color:#3730a3;padding:2px 6px;border-radius:999px;margin-left:6px;border:1px solid #c7d2fe}
h3{margin:0 0 8px 0;font-size:16px}
</style>
</head>
<body>
<div class="wrap">
  <h2>문자 발송(웹) · 선생님/담당학생 자동 로딩</h2>
  <p class="muted">서버의 <code>/api/roster</code>에서 교사/학생 목록을 자동 불러옵니다. 필요 시 CSV 업로드로 덮어쓸 수 있어요.</p>

  <!-- 서버/보안/설정 -->
  <div class="card">
    <div class="row">
      <div class="col">
        <label>서버 설정</label>
        <input id="fromNum" disabled>
        <div id="cfgInfo" class="muted mt8">서버 설정 로딩 중...</div>
      </div>
      <div class="col">
        <label>보안 토큰 (선택)</label>
        <input id="token" placeholder="AUTH_TOKEN 사용 시 입력 (예: mytoken)">
        <div class="muted mt8">서버에 AUTH_TOKEN이 설정되었다면 발송 시 Authorization 헤더를 첨부합니다.</div>
      </div>
      <div class="col">
        <label>드라이런(dry-run)</label>
        <div class="row">
          <input type="checkbox" id="dry" />
          <span class="muted">체크 시 실제 발송 없이 요청/응답만 확인</span>
        </div>
      </div>
    </div>
  </div>

  <!-- 자동 로스터 + 수동 CSV -->
  <div class="card mt16">
    <h3>1) 선생님/담당학생</h3>
    <div class="row">
      <div class="col">
        <label>자동 로드 상태</label>
        <div id="rosterInfo" class="muted">/api/roster 호출 대기 중...</div>
      </div>
      <div class="col">
        <label>수동 CSV 업로드(덮어쓰기)</label>
        <input type="file" id="csv" accept=".csv,text/csv">
        <div class="muted mt8">헤더 예시: <b>담당선생</b>, <b>학생이름</b>, <b>학부모전화</b>, <b>학생전화</b></div>
      </div>
      <div class="col">
        <label>검색(학생)</label>
        <input id="search" placeholder="이름 일부로 필터링">
      </div>
    </div>

    <div class="mt12">
      <label>선생님 선택</label>
      <div id="teacherBox" class="grid teachers"></div>
    </div>
    <div class="mt12">
      <label>담당 학생</label>
      <div id="studentBox" class="grid students"></div>
      <div class="muted mt8">학생 버튼 클릭 → 수신번호 자동 선택</div>
    </div>
  </div>

  <!-- 수신대상/문구/발송 -->
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
      <textarea id="text" placeholder="{name} 자리표시자는 학생 이름으로 치환됩니다."></textarea>
      <div class="muted mt8">미리보기: <span id="preview"></span></div>
    </div>
    <div class="row mt16">
      <button id="send" class="primary">전송</button>
      <span id="status" class="muted"></span>
    </div>
    <div class="mt12">
      <label>결과</label>
      <pre id="out">(아직 없음)</pre>
    </div>
  </div>
</div>

<script>
const STORAGE_KEY_LAST   = "sms_ui_last_teacher_v1";
const TEMPLATES = [
  { label:"미등원 안내", text:"{name} 학생 아직 등원하지 않았습니다. 확인 부탁드립니다." },
  { label:"지각 안내",  text:"{name} 학생이 지각 중입니다. 10분 내 등원 예정인가요?" },
  { label:"조퇴 안내",  text:"{name} 학생 오늘 조퇴하였습니다. 귀가 시간 확인 부탁드립니다." },
  { label:"숙제 미제출", text:"{name} 학생 오늘 숙제 미제출입니다. 가정에서 점검 부탁드립니다." },
  { label:"수업 공지",  text:"{name} 학생 금일 수업 관련 안내드립니다: " }
];
const onlyDigits = s => (s||"").replace(/\\D/g,"");
const norm = s => { const d=onlyDigits(s); if(d.length===11) return d.replace(/(\\d{3})(\\d{4})(\\d{4})/,"$1-$2-$3"); if(d.length===10) return d.replace(/(\\d{2,3})(\\d{3,4})(\\d{4})/,"$1-$2-$3"); return s||""; };
const $  = sel => document.querySelector(sel);
const $$ = sel => Array.from(document.querySelectorAll(sel));

const state = {
  provider:"", defaultFrom:"",
  roster:{}, teacherList:[], currentTeacher:"", currentStudent:null,
  toType:"parent"
};

async function loadConfig(){
  try{ const r=await fetch("/api/sms/config"); const cfg=await r.json();
    state.provider=cfg.provider||""; state.defaultFrom=String(cfg.defaultFrom||"");
    $("#fromNum").value=state.defaultFrom||"(서버 미설정)";
    $("#cfgInfo").textContent=`provider: ${state.provider||"unknown"}`;
  }catch{ $("#cfgInfo").textContent="서버 설정을 불러오지 못했습니다."; }
}

async function autoLoadRoster(){
  try{
    const r = await fetch("/api/roster");
    const data = await r.json();
    if(!r.ok || !data.ok) throw new Error(data.error||"no roster");
    state.roster = data.roster||{};
    state.teacherList = data.teachers||Object.keys(state.roster);
    const last = localStorage.getItem(STORAGE_KEY_LAST);
    state.currentTeacher = (last && state.roster[last]) ? last : (state.teacherList[0]||"");
    $("#rosterInfo").textContent = `자동 로드 성공 (source: ${data.source||"unknown"}) · 교사 ${state.teacherList.length}명`;
    renderTeachers(); renderStudents(); updatePreview();
  }catch(e){
    $("#rosterInfo").textContent = "자동 로드 실패(/api/roster). CSV 업로드로 불러오세요.";
  }
}

function setupTemplates(){
  const box=$("#tpls"); box.innerHTML="";
  TEMPLATES.forEach(t=>{
    const b=document.createElement("button");
    b.className="pill"; b.textContent=t.label;
    b.addEventListener("click",()=>{
      if(state.currentStudent){
        $("#text").value=t.text.replaceAll("{name}", state.currentStudent.name||"");
      }else{ $("#text").value=t.text; }
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
  if(!state.teacherList.length){ box.innerHTML='<span class="muted">교사 데이터가 없습니다.</span>'; return; }
  state.teacherList.forEach(t=>{
    const b=document.createElement("button");
    b.className="pill"+(t===state.currentTeacher?" on":"");
    const cnt=(state.roster[t]||[]).length;
    b.innerHTML=`${t}<span class="badge">${cnt}</span>`;
    b.addEventListener("click",()=>{
      state.currentTeacher=t; localStorage.setItem(STORAGE_KEY_LAST,t);
      renderTeachers(); renderStudents();
    });
    box.appendChild(b);
  });
}

function renderStudents(){
  const box=$("#studentBox"); box.innerHTML="";
  const list = (state.roster[state.currentTeacher]||[]);
  const q = ($("#search").value||"").trim();
  const filtered = q ? list.filter(s=>s.name && s.name.includes(q)) : list;
  if(!filtered.length){ box.innerHTML='<span class="muted">학생이 없습니다.</span>'; state.currentStudent=null; updatePreview(); return; }
  filtered.forEach(s=>{
    const b=document.createElement("button");
    b.className="pill"+(state.currentStudent&&state.currentStudent.id===s.id?" on":"");
    const phone = norm(s.parentPhone)||norm(s.studentPhone)||"-";
    b.innerHTML = `${s.name}<span class="badge">${phone}</span>`;
    b.addEventListener("click",()=>{
      state.currentStudent=s; updatePreview();
      if(!$("#text").value.trim()){ $("#text").value=TEMPLATES[0].text.replaceAll("{name}", s.name||""); updatePreview(); }
      renderStudents();
    });
    box.appendChild(b);
  });
}

function updatePreview(){
  const s=state.currentStudent;
  $("#toPreview").textContent = computeTo()||"-";
  const txt=$("#text").value||"";
  $("#preview").textContent = (txt||"").replaceAll("{name}", s?.name||"");
}

function computeTo(){
  if(state.toType==="custom") return norm($("#customTo").value||"");
  const s=state.currentStudent; if(!s) return "";
  if(state.toType==="parent")  return norm(s.parentPhone||"");
  if(state.toType==="student") return norm(s.studentPhone||"");
  return "";
}

// manual CSV override
function hookCSV(){
  $("#csv").addEventListener("change",(ev)=>{
    const f=ev.target.files?.[0]; if(!f) return;
    const fr=new FileReader();
    fr.onload=()=>{ try{
      const rows=parseCSV(String(fr.result||""));
      const built=buildRoster(rows);
      state.roster=built.roster; state.teacherList=built.teachers;
      state.currentTeacher=state.teacherList[0]||"";
      $("#rosterInfo").textContent = `수동 CSV 업로드 완료 · 교사 ${state.teacherList.length}명`;
      renderTeachers(); renderStudents(); updatePreview();
    }catch(e){ alert("CSV 파싱 실패: "+e); } };
    fr.readAsText(f,"utf-8");
  });
  $("#search").addEventListener("input", renderStudents);
}

function keyify(h){ return String(h||"").toLowerCase().replace(/\\s+/g,"").replace(/[^\\w가-힣]/g,""); }
function parseCSV(text){
  const rows=[]; let row=[], cur="", inQ=false;
  for(let i=0;i<text.length;i++){
    const ch=text[i], nx=text[i+1];
    if(inQ){ if(ch=='"'&&nx=='"'){cur+='"';i++;} else if(ch=='"'){inQ=false;} else cur+=ch; }
    else{ if(ch=='"'){inQ=true;} else if(ch===','){row.push(cur);cur="";} else if(ch==='\\n'){row.push(cur);rows.push(row);row=[];cur="";} else if(ch==='\\r'){ } else cur+=ch; }
  }
  if(cur.length>0 || row.length>0){ row.push(cur); rows.push(row); }
  return rows;
}
function detectColumns(headers){
  const H=headers.map(keyify);
  const find=(cands)=>{ for(let i=0;i<H.length;i++){ if(cands.includes(H[i])) return i; } return -1; };
  return {
    teacher: find(["담당","담당선생","담당선생님","선생님","teacher","tch","담당자"]),
    name   : find(["학생이름","이름","name","student","학생","성명"]),
    parent : find(["학부모전화","학부모연락처","부모전화","보호자전화","보호자연락처","parent","parentphone"]),
    student: find(["학생전화","연락처","student","studentphone","전화번호","핸드폰","휴대폰","mobile","cell"]),
  };
}
function buildRoster(rows){
  if(!rows.length) return {roster:{}, teachers:[]};
  const headers=rows[0]; const idx=detectColumns(headers);
  if(idx.teacher<0 || idx.name<0) throw new Error("헤더 인식 실패(담당선생/학생이름 필요)");
  const roster={}, ts=new Set();
  for(let r=1;r<rows.length;r++){
    const cols=rows[r]; if(!cols||cols.length<2) continue;
    const teacher=String(cols[idx.teacher]||"").trim(); if(!teacher) continue;
    const name   =String(cols[idx.name]||"").trim();    if(!name) continue;
    const parent = idx.parent>=0 ? (cols[idx.parent]||"") : "";
    const student= idx.student>=0? (cols[idx.student]||""): "";
    const obj={id:`${teacher}::${name}::${r}`, name, parentPhone:onlyDigits(parent), studentPhone:onlyDigits(student)};
    roster[teacher]=roster[teacher]||[]; roster[teacher].push(obj); ts.add(teacher);
  }
  const teachers=[...ts].sort((a,b)=>a.localeCompare(b,"ko"));
  for(const t of teachers){ roster[t].sort((a,b)=>a.name.localeCompare(b.name,"ko")); }
  return {roster, teachers};
}
function onlyDigits(s){ return (s||"").replace(/\\D/g,""); }

async function send(){
  const token=($("#token").value||"").trim();
  const headers={"Content-Type":"application/json"}; if(token) headers["Authorization"]="Bearer "+token;

  const to=onlyDigits(computeTo());
  const from=onlyDigits(state.defaultFrom||"");
  const s=state.currentStudent;
  const text=($("#text").value||"").replaceAll("{name}", s?.name||"");
  const dry=$("#dry").checked;

  $("#status").textContent="전송 중...";
  if(!s){ alert("학생을 먼저 선택하세요."); $("#status").textContent=""; return; }
  if(!to){ alert("수신 번호가 비어있습니다."); $("#status").textContent=""; return; }
  if(!text.trim()){ alert("문자 내용을 입력하세요."); $("#status").textContent=""; return; }

  const payload={to,from,text,student:s.name,dry};
  try{
    const r=await fetch("/api/sms",{method:"POST",headers,body:JSON.stringify(payload)});
    const data=await r.json().catch(()=>({ok:false,status:r.status}));
    $("#out").textContent=JSON.stringify(data,null,2);
    $("#status").textContent=r.ok?(dry?"드라이런 완료":"전송 요청 완료"):"전송 실패";
  }catch(e){ $("#out").textContent=String(e); $("#status").textContent="오류"; }
}

// init
(async function(){
  await loadConfig();
  setupTemplates();
  setupToType();
  hookCSV();
  await autoLoadRoster();  // 기본: 서버에서 자동 로드 시도
  $("#text").addEventListener("input", updatePreview);
  $("#send").addEventListener("click", send);
})();
</script>
</body></html>
"""

@app.get("/ui")
def ui():
    return Response(WEB_UI_HTML, mimetype="text/html; charset=utf-8")

@app.get("/favicon.ico")
def _favicon():
    return ("", 204)

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    print("== URL MAP ==")
    print(app.url_map)
    app.run(host="0.0.0.0", port=port, debug=False)
