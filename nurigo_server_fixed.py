# -*- coding: utf-8 -*-
"""
Nurigo/Solapi SMS proxy (Flask)

Endpoints
  GET  /                   -> health
  GET  /routes             -> list routes (debug)
  GET  /api/sms/config     -> {"provider": "...", "defaultFrom": "010..."}
  POST /api/sms            -> {to, from, text, dry?}
  GET  /ui                 -> simple web UI

Env Vars
  PORT            : bind port (Render sets this automatically)
  DEFAULT_SENDER  : default "from" number (e.g., 01080348069)
  SOLAPI_KEY      : Solapi API key (use if not forwarding)
  SOLAPI_SECRET   : Solapi API secret
  FORWARD_URL     : if set, forward JSON to this URL instead of calling Solapi
  AUTH_TOKEN      : if set, require header "Authorization: Bearer <AUTH_TOKEN>"
"""
import os, json, hmac, hashlib, secrets, requests
from datetime import datetime, timezone

from flask import Flask, request, jsonify, Response
from flask_cors import CORS

app = Flask(__name__)
CORS(app)  # tighten allowed origins in production

DEFAULT_SENDER = os.getenv("DEFAULT_SENDER", "").strip()
FORWARD_URL    = os.getenv("FORWARD_URL", "").strip()
SOLAPI_KEY     = os.getenv("SOLAPI_KEY", "").strip()
SOLAPI_SECRET  = os.getenv("SOLAPI_SECRET", "").strip()
AUTH_TOKEN     = os.getenv("AUTH_TOKEN", "").strip()

def current_provider() -> str:
    if FORWARD_URL:
        return "forward"
    if SOLAPI_KEY and SOLAPI_SECRET:
        return "solapi"
    return "mock"

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
<title>SMS 테스트 (웹)</title>
<style>
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Apple SD Gothic Neo,Noto Sans KR,Arial,sans-serif;background:#f8fafc;margin:0}
.wrap{max-width:840px;margin:24px auto;padding:16px}
.card{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:16px;box-shadow:0 1px 2px rgba(0,0,0,.04)}
.row{display:flex;gap:12px;flex-wrap:wrap;align-items:center}
.col{flex:1 1 240px;min-width:240px}
label{display:block;font-size:12px;color:#334155;margin-bottom:6px}
input,select,textarea{width:100%;padding:10px 12px;border:1px solid #cbd5e1;border-radius:10px;font-size:14px}
textarea{min-height:120px}
button{padding:10px 14px;border-radius:10px;border:1px solid #cbd5e1;background:#fff;cursor:pointer}
button.primary{background:#2563eb;color:#fff;border-color:#2563eb}
.pill{padding:6px 10px;border-radius:999px;border:1px solid #cbd5e1;background:#fff;font-size:12px;cursor:pointer}
.pill.on{background:#0ea5e9;color:#fff;border-color:#0ea5e9}
.muted{color:#64748b;font-size:12px}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
.templates{display:flex;flex-wrap:wrap;gap:8px}
.mt8{margin-top:8px}.mt12{margin-top:12px}.mt16{margin-top:16px}.mt24{margin-top:24px}
pre{background:#0b1020;color:#c7d2fe;padding:12px;border-radius:10px;overflow:auto}
</style>
</head>
<body>
<div class="wrap">
  <h2>SMS 테스트 (웹)</h2>
  <p class="muted">같은 오리진의 <code>/api/sms</code>와 <code>/api/sms/config</code>를 호출합니다.</p>
  <div class="card">
    <div class="row">
      <div class="col">
        <label>서버 설정</label>
        <input id="fromNum" disabled>
        <div id="cfgInfo" class="muted mt8">서버 설정을 불러오는 중...</div>
      </div>
      <div class="col">
        <label>학생 선택</label>
        <select id="studentSel"></select>
      </div>
    </div>

    <div class="grid mt16">
      <div>
        <label>수신 대상</label>
        <div class="row">
          <span class="pill on" data-to="parent">학부모</span>
          <span class="pill" data-to="student">학생</span>
          <span class="pill" data-to="custom">직접</span>
          <input id="customTo" placeholder="직접 입력 (예: 01012345678)" style="display:none;flex:1 1 240px">
        </div>
        <div class="muted mt8">현재 수신번호: <b id="toPreview">-</b></div>
      </div>
      <div>
        <label>드라이런(dry-run)</label>
        <div class="row">
          <input type="checkbox" id="dry" />
          <span class="muted">체크 시 실제 발송 없이 요청/응답만 확인</span>
        </div>
      </div>
    </div>

    <div class="mt16">
      <label>원클릭 문구</label>
      <div class="templates" id="tpls"></div>
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

    <div class="mt16">
      <label>결과</label>
      <pre id="out">(아직 없음)</pre>
    </div>
  </div>
</div>

<script>
const STORAGE_KEY="student_mgmt_standalone_v0_3_2";
const TEMPLATES=[
 {label:"미등원 안내",text:"{name} 학생 아직 등원하지 않았습니다. 확인 부탁드립니다."},
 {label:"지각 안내", text:"{name} 학생이 지각 중입니다. 10분 내 등원 예정인가요?"},
 {label:"조퇴 안내", text:"{name} 학생 오늘 조퇴하였습니다. 귀가 시간 확인 부탁드립니다."},
 {label:"숙제 미제출",text:"{name} 학생 오늘 숙제 미제출입니다. 가정에서 점검 부탁드립니다."},
 {label:"수업 공지", text:"{name} 학생 금일 수업 관련 안내드립니다: "}
];
const onlyDigits=s=>(s||"").replace(/\\D/g,"");
const norm=s=>{const d=onlyDigits(s);if(d.length===11)return d.replace(/(\\d{3})(\\d{4})(\\d{4})/,"$1-$2-$3");if(d.length===10)return d.replace(/(\\d{2,3})(\\d{3,4})(\\d{4})/,"$1-$2-$3");return s||""};
const $=sel=>document.querySelector(sel);
const $$=sel=>Array.from(document.querySelectorAll(sel));
const state={list:[],toType:"parent",defaultFrom:""};

async function loadConfig(){
  try{
    const r=await fetch("/api/sms/config");
    if(!r.ok)throw new Error("bad config");
    const cfg=await r.json();
    state.defaultFrom=String(cfg.defaultFrom||"");
    $("#fromNum").value=state.defaultFrom||"(서버 미설정)";
    $("#cfgInfo").textContent="provider: "+(cfg.provider||"unknown");
  }catch(e){$("#cfgInfo").textContent="서버 설정을 불러오지 못했습니다."}
}
function loadStudents(){
  try{
    const raw=localStorage.getItem(STORAGE_KEY);
    if(raw){const arr=JSON.parse(raw);if(Array.isArray(arr)&&arr.length)state.list=arr;}
  }catch(e){}
  if(!state.list.length){
    state.list=[
      {id:"1",name:"김하늘",parentPhone:"01011112222",studentPhone:"01033334444"},
      {id:"2",name:"이도윤",parentPhone:"01055556666",studentPhone:"01077778888"},
      {id:"3",name:"박서연",parentPhone:"01099990000",studentPhone:"01012123434"}
    ];
  }
  const sel=$("#studentSel");
  sel.innerHTML=state.list.map(s=>`<option value="${s.id}">${s.name}</option>`).join("");
}
function currentStudent(){const id=$("#studentSel").value;return state.list.find(x=>x.id===id)||state.list[0]}
function toNumber(){const s=currentStudent();if(state.toType==="parent")return norm(s?.parentPhone||"");if(state.toType==="student")return norm(s?.studentPhone||"");return norm($("#customTo").value||"")}
function updatePreview(){const s=currentStudent();$("#toPreview").textContent=toNumber()||"-";const txt=$("#text").value||"";$("#preview").textContent=(txt||"").replaceAll("{name}",s?.name||"")}
function setupToType(){$$(".pill").forEach(p=>{p.addEventListener("click",()=>{$$(".pill").forEach(x=>x.classList.remove("on"));p.classList.add("on");state.toType=p.dataset.to;const isCustom=state.toType==="custom";$("#customTo").style.display=isCustom?"block":"none";updatePreview();});});}
function setupTemplates(){const box=$("#tpls");box.innerHTML="";TEMPLATES.forEach(t=>{const b=document.createElement("button");b.className="pill";b.textContent=t.label;b.addEventListener("click",()=>{const name=currentStudent()?.name||"";$("#text").value=t.text.replaceAll("{name}",name);updatePreview();});box.appendChild(b);});}
async function send(){
  const from=onlyDigits(state.defaultFrom||""); // use server default
  const to=onlyDigits(toNumber());
  const s=currentStudent();
  const text=($("#text").value||"").replaceAll("{name}",s?.name||"");
  const dry=$("#dry").checked;

  $("#status").textContent="전송 중...";
  if(!to){alert("수신 번호가 비어있습니다.");$("#status").textContent="";return;}
  if(!text.trim()){alert("문자 내용을 입력하세요.");$("#status").textContent="";return;}

  const payload={to,from,text,student:s?.name,dry};
  try{
    const r=await fetch("/api/sms",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    const data=await r.json().catch(()=>({ok:false,status:r.status}));
    $("#out").textContent=JSON.stringify(data,null,2);
    $("#status").textContent=r.ok?"전송 요청 완료":"전송 실패";
  }catch(e){$("#out").textContent=String(e);$("#status").textContent="오류";}
}
loadConfig();loadStudents();setupToType();setupTemplates();updatePreview();
$("#studentSel").addEventListener("change",updatePreview);
$("#customTo").addEventListener("input",updatePreview);
$("#text").addEventListener("input",updatePreview);
$("#send").addEventListener("click",send);
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
