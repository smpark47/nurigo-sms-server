# -*- coding: utf-8 -*-
"""
Nurigo/Solapi SMS proxy (Flask) - minimal dry-run with Safari/mobile fixes

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
CORS(app)

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
    if not ok: return err
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

    if dry:
        return jsonify({
            "ok": True, "provider": "mock", "dry": True,
            "echo": {"to": to, "from": from_num, "text": text, "len": len(text)},
            "at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        })

    if FORWARD_URL:
        try:
            r = requests.post(FORWARD_URL, json={"to": to, "from": from_num, "text": text}, timeout=15)
            return (r.text, r.status_code, {"Content-Type": r.headers.get("Content-Type", "application/json")})
        except Exception as e:
            return jsonify({"ok": False, "error": "forward-failed", "detail": str(e)}), 502

    if SOLAPI_KEY and SOLAPI_SECRET:
        try:
            date_time = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            salt = secrets.token_hex(16)
            signature = hmac.new(SOLAPI_SECRET.encode("utf-8"), (date_time + salt).encode("utf-8"), hashlib.sha256).hexdigest()
            auth_header = f"HMAC-SHA256 apiKey={SOLAPI_KEY}, date={date_time}, salt={salt}, signature={signature}"
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

    return jsonify({
        "ok": True, "provider": "mock", "dry": True,
        "echo": {"to": to, "from": from_num, "text": text, "len": len(text)},
        "at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    })

# --- Simple Web UI ---
WEB_UI_HTML = r"""<!doctype html>
<html lang="ko"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>서울더함수학학원 문자 전송 프로그램</title>
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

/* ✅ Safari gap issue: remove gap inside inlinecheck and use precise margin */
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

/* ✅ status text doesn't overlap; responsive placement */
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
</style>
</head>
<body>
<div class="wrap">
  <h2>서울더함수학학원 문자 전송 프로그램</h2>

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

    <!-- Send row: button + (checkbox + text only) -->
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
</div>

<script>
// ===== ROSTER: from roster.csv (박선민/주말반쌤 제외) =====
const ROSTER = {
  "최윤영": [
    {"id": "최윤영::기도윤", "name": "기도윤", "parentPhone": "01047612937", "studentPhone": "01057172937"},
    {"id": "최윤영::황세빈", "name": "황세빈", "parentPhone": "01029340929", "studentPhone": ""},
    {"id": "최윤영::최시원", "name": "최시원", "parentPhone": "01091925924", "studentPhone": ""},
    {"id": "최윤영::이동현", "name": "이동현", "parentPhone": "01095905486", "studentPhone": ""},
    {"id": "최윤영::이소영", "name": "이소영", "parentPhone": "01080253405", "studentPhone": ""},
    {"id": "최윤영::최현서", "name": "최현서", "parentPhone": "01026618590", "studentPhone": ""},
    {"id": "최윤영::신유나", "name": "신유나", "parentPhone": "01099245907", "studentPhone": ""},
    {"id": "최윤영::신유찬", "name": "신유찬", "parentPhone": "01099245907", "studentPhone": ""},
    {"id": "최윤영::정준영", "name": "정준영", "parentPhone": "01087429022", "studentPhone": ""},
    {"id": "최윤영::노유종", "name": "노유종", "parentPhone": "01047626707", "studentPhone": ""},
    {"id": "최윤영::정다율", "name": "정다율", "parentPhone": "01050531629", "studentPhone": ""},
    {"id": "최윤영::조정운", "name": "조정운", "parentPhone": "01074321567", "studentPhone": ""},
    {"id": "최윤영::최성현", "name": "최성현", "parentPhone": "01037465003", "studentPhone": ""},
    {"id": "최윤영::유하엘", "name": "유하엘", "parentPhone": "01035796389", "studentPhone": ""},
    {"id": "최윤영::이수빈", "name": "이수빈", "parentPhone": "", "studentPhone": ""},
    {"id": "최윤영::김범준", "name": "김범준", "parentPhone": "01036297472", "studentPhone": ""},
    {"id": "최윤영::김지환", "name": "김지환", "parentPhone": "01085822669", "studentPhone": ""},
    {"id": "최윤영::김강휘", "name": "김강휘", "parentPhone": "01091263383", "studentPhone": ""},
    {"id": "최윤영::이채은", "name": "이채은", "parentPhone": "01066394676", "studentPhone": ""},
    {"id": "최윤영::하유찬", "name": "하유찬", "parentPhone": "01075571627", "studentPhone": ""},
    {"id": "최윤영::정유준", "name": "정유준", "parentPhone": "01090443436", "studentPhone": ""},
    {"id": "최윤영::안치현", "name": "안치현", "parentPhone": "01040227709", "studentPhone": ""},
    {"id": "최윤영::고결", "name": "고결", "parentPhone": "01036179299", "studentPhone": ""},
    {"id": "최윤영::이현범", "name": "이현범", "parentPhone": "01094312256", "studentPhone": ""},
    {"id": "최윤영::현가비", "name": "현가비", "parentPhone": "01094083490", "studentPhone": ""},
    {"id": "최윤영::이연우", "name": "이연우", "parentPhone": "01030698339", "studentPhone": ""},
    {"id": "최윤영::정해수", "name": "정해수", "parentPhone": "01040782250", "studentPhone": ""},
    {"id": "최윤영::범정우", "name": "범정우", "parentPhone": "01035988684", "studentPhone": ""},
    {"id": "최윤영::채정원", "name": "채정원", "parentPhone": "01063034167", "studentPhone": ""}
  ],
  "이헌철": [
    {"id": "이헌철::민윤서", "name": "민윤서", "parentPhone": "01054043786", "studentPhone": ""},
    {"id": "이헌철::임창빈", "name": "임창빈", "parentPhone": "01041227964", "studentPhone": ""},
    {"id": "이헌철::김시연", "name": "김시연", "parentPhone": "01086701915", "studentPhone": ""},
    {"id": "이헌철::박준형", "name": "박준형", "parentPhone": "01053752902", "studentPhone": ""},
    {"id": "이헌철::최윤겸", "name": "최윤겸", "parentPhone": "01020932459", "studentPhone": ""},
    {"id": "이헌철::김온유", "name": "김온유", "parentPhone": "01030333232", "studentPhone": ""},
    {"id": "이헌철::김건우", "name": "김건우", "parentPhone": "01090952844", "studentPhone": ""},
    {"id": "이헌철::조석현", "name": "조석현", "parentPhone": "01025104035", "studentPhone": ""},
    {"id": "이헌철::봉유근", "name": "봉유근", "parentPhone": "01043377107", "studentPhone": ""},
    {"id": "이헌철::윤서영", "name": "윤서영", "parentPhone": "01072093663", "studentPhone": ""},
    {"id": "이헌철::고준서", "name": "고준서", "parentPhone": "01097905478", "studentPhone": ""},
    {"id": "이헌철::곽민서", "name": "곽민서", "parentPhone": "01044746152", "studentPhone": ""},
    {"id": "이헌철::백소율", "name": "백소율", "parentPhone": "01099537571", "studentPhone": ""},
    {"id": "이헌철::유현빈", "name": "유현빈", "parentPhone": "01091151908", "studentPhone": ""},
    {"id": "이헌철::신은재", "name": "신은재", "parentPhone": "01073810826", "studentPhone": ""},
    {"id": "이헌철::연정흠", "name": "연정흠", "parentPhone": "01054595704", "studentPhone": ""},
    {"id": "이헌철::유강민", "name": "유강민", "parentPhone": "01089309296", "studentPhone": ""},
    {"id": "이헌철::남이준", "name": "남이준", "parentPhone": "01049477172", "studentPhone": ""},
    {"id": "이헌철::이현", "name": "이현", "parentPhone": "01083448867", "studentPhone": ""},
    {"id": "이헌철::정유진", "name": "정유진", "parentPhone": "01033898056", "studentPhone": ""},
    {"id": "이헌철::전찬식", "name": "전찬식", "parentPhone": "01066073353", "studentPhone": ""},
    {"id": "이헌철::김주환", "name": "김주환", "parentPhone": "01037602796", "studentPhone": ""},
    {"id": "이헌철::김수현", "name": "김수현", "parentPhone": "01034667951", "studentPhone": ""},
    {"id": "이헌철::김도윤", "name": "김도윤", "parentPhone": "01090952844", "studentPhone": ""},
    {"id": "이헌철::김도현", "name": "김도현", "parentPhone": "01044087732", "studentPhone": ""},
    {"id": "이헌철::이유근", "name": "이유근", "parentPhone": "01027106068", "studentPhone": ""},
    {"id": "이헌철::변진우", "name": "변진우", "parentPhone": "01034314850", "studentPhone": ""},
    {"id": "이헌철::장민경", "name": "장민경", "parentPhone": "01066741973", "studentPhone": ""},
    {"id": "이헌철::홍가은", "name": "홍가은", "parentPhone": "01094178304", "studentPhone": ""},
    {"id": "이헌철::윤대철", "name": "윤대철", "parentPhone": "01091337052", "studentPhone": ""},
    {"id": "이헌철::정지후", "name": "정지후", "parentPhone": "01050362312", "studentPhone": ""},
    {"id": "이헌철::김기범", "name": "김기범", "parentPhone": "01051881350", "studentPhone": ""},
    {"id": "이헌철::고하은", "name": "고하은", "parentPhone": "01036245135", "studentPhone": ""},
    {"id": "이헌철::송유담", "name": "송유담", "parentPhone": "01093940117", "studentPhone": ""},
    {"id": "이헌철::송유현", "name": "송유현", "parentPhone": "01088081413", "studentPhone": ""},
    {"id": "이헌철::장민아", "name": "장민아", "parentPhone": "01049404508", "studentPhone": ""},
    {"id": "이헌철::유재훈", "name": "유재훈", "parentPhone": "01033838321", "studentPhone": ""}
  ],
  "장호민": [
    {"id": "장호민::정윤슬", "name": "정윤슬", "parentPhone": "01051050952", "studentPhone": ""},
    {"id": "장호민::김리우", "name": "김리우", "parentPhone": "01077214721", "studentPhone": ""},
    {"id": "장호민::최설아", "name": "최설아", "parentPhone": "01037686015", "studentPhone": ""},
    {"id": "장호민::전태식", "name": "전태식", "parentPhone": "01066073353", "studentPhone": ""},
    {"id": "장호민::김민균", "name": "김민균", "parentPhone": "01055068033", "studentPhone": ""},
    {"id": "장호민::박서윤", "name": "박서윤", "parentPhone": "01065333681", "studentPhone": ""},
    {"id": "장호민::전아인", "name": "전아인", "parentPhone": "01040040318", "studentPhone": ""},
    {"id": "장호민::전연호", "name": "전연호", "parentPhone": "01097072353", "studentPhone": ""},
    {"id": "장호민::이현은", "name": "이현은", "parentPhone": "01062651516", "studentPhone": ""},
    {"id": "장호민::박혜윤", "name": "박혜윤", "parentPhone": "01026661892", "studentPhone": ""},
    {"id": "장호민::하지우", "name": "하지우", "parentPhone": "01044217783", "studentPhone": ""},
    {"id": "장호민::이예준", "name": "이예준", "parentPhone": "01027000526", "studentPhone": ""},
    {"id": "장호민::이채라", "name": "이채라", "parentPhone": "", "studentPhone": ""},
    {"id": "장호민::김서연", "name": "김서연", "parentPhone": "01092437376", "studentPhone": ""},
    {"id": "장호민::옥범준", "name": "옥범준", "parentPhone": "01096733240", "studentPhone": ""},
    {"id": "장호민::조성훈", "name": "조성훈", "parentPhone": "01020714311", "studentPhone": ""},
    {"id": "장호민::오지연", "name": "오지연", "parentPhone": "01044192557", "studentPhone": ""},
    {"id": "장호민::임가은", "name": "임가은", "parentPhone": "01098489802", "studentPhone": ""},
    {"id": "장호민::이하람", "name": "이하람", "parentPhone": "01026156343", "studentPhone": ""},
    {"id": "장호민::김도원", "name": "김도원", "parentPhone": "01033386763", "studentPhone": ""},
    {"id": "장호민::권은유", "name": "권은유", "parentPhone": "01094115087", "studentPhone": ""},
    {"id": "장호민::강현준", "name": "강현준", "parentPhone": "01075672641", "studentPhone": ""},
    {"id": "장호민::이준근", "name": "이준근", "parentPhone": "01066245875", "studentPhone": ""},
    {"id": "장호민::송유민", "name": "송유민", "parentPhone": "01088081413", "studentPhone": ""},
    {"id": "장호민::이태우", "name": "이태우", "parentPhone": "01051773239", "studentPhone": ""},
    {"id": "장호민::이서윤", "name": "이서윤", "parentPhone": "01023552566", "studentPhone": ""},
    {"id": "장호민::전예솔", "name": "전예솔", "parentPhone": "01046413697", "studentPhone": ""},
    {"id": "장호민::김재운", "name": "김재운", "parentPhone": "01086701915", "studentPhone": ""},
    {"id": "장호민::김주안", "name": "김주안", "parentPhone": "01090891156", "studentPhone": ""},
    {"id": "장호민::이건우", "name": "이건우", "parentPhone": "01030698339", "studentPhone": ""},
    {"id": "장호민::정민우", "name": "정민우", "parentPhone": "01050531629", "studentPhone": ""},
    {"id": "장호민::박윤지", "name": "박윤지", "parentPhone": "01054697072", "studentPhone": ""}
  ],
  "황재선": [
    {"id": "황재선::김다윤", "name": "김다윤", "parentPhone": "01098400503", "studentPhone": ""},
    {"id": "황재선::신지우", "name": "신지우", "parentPhone": "01042367667", "studentPhone": ""},
    {"id": "황재선::안준혁", "name": "안준혁", "parentPhone": "01027459771", "studentPhone": ""},
    {"id": "황재선::강이현", "name": "강이현", "parentPhone": "01030522547", "studentPhone": ""},
    {"id": "황재선::장지후", "name": "장지후", "parentPhone": "01066741973", "studentPhone": ""},
    {"id": "황재선::권민결", "name": "권민결", "parentPhone": "01045723566", "studentPhone": ""},
    {"id": "황재선::황서현", "name": "황서현", "parentPhone": "01039054973", "studentPhone": ""},
    {"id": "황재선::임하준", "name": "임하준", "parentPhone": "01048557183", "studentPhone": ""},
    {"id": "황재선::안치운", "name": "안치운", "parentPhone": "01027440458", "studentPhone": ""},
    {"id": "황재선::김리안", "name": "김리안", "parentPhone": "01067188016", "studentPhone": ""},
    {"id": "황재선::김예준", "name": "김예준", "parentPhone": "01045876999", "studentPhone": ""},
    {"id": "황재선::신준화", "name": "신준화", "parentPhone": "01038382098", "studentPhone": ""},
    {"id": "황재선::양승일", "name": "양승일", "parentPhone": "01090125412", "studentPhone": ""},
    {"id": "황재선::이채영", "name": "이채영", "parentPhone": "01035201122", "studentPhone": ""}
  ]
};
// ===== helper functions =====
["박선민","주말반쌤"].forEach(k => { if (ROSTER[k]) delete ROSTER[k]; });

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
  currentTeacher: "",
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

  const payload={to,from,text,student:s.name,dry};
  try{
    const r=await fetch("/api/sms",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    const data=await r.json().catch(()=>({ok:false,status:r.status}));
    $("#out").textContent=JSON.stringify(data,null,2);
    $("#status").textContent=r.ok?(dry?"드라이런 완료":"전송 요청 완료"):"전송 실패";
  }catch(e){
    $("#out").textContent=String(e);
    $("#status").textContent="오류";
  }
}

// init
(async function(){
  await loadConfig();
  setupTemplates();
  setupToType();

  state.teacherList = Object.keys(state.roster);
  state.currentTeacher = state.teacherList[0] || "";
  renderTeachers(); renderStudents(); updatePreview();

  $("#search").addEventListener("input", renderStudents);
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
