# -*- coding: utf-8 -*-
import os, json, hmac, hashlib, secrets, requests
from datetime import datetime, timezone
from flask import Flask, request, jsonify, Response, send_from_directory
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

DEFAULT_SENDER = os.getenv("DEFAULT_SENDER", "").strip()
FORWARD_URL    = os.getenv("FORWARD_URL", "").strip()
SOLAPI_KEY     = os.getenv("SOLAPI_KEY", "").strip()
SOLAPI_SECRET  = os.getenv("SOLAPI_SECRET", "").strip()
AUTH_TOKEN     = os.getenv("AUTH_TOKEN", "").strip()

def current_provider() -> str:
    if FORWARD_URL: return "forward"
    if SOLAPI_KEY and SOLAPI_SECRET: return "solapi"
    return "mock"

@app.get("/")
def root():
    return {"ok": True, "service": "nurigo-sms-proxy", "provider": current_provider()}, 200

@app.get("/api/sms/config")
def sms_config():
    return jsonify({"provider": current_provider(), "defaultFrom": DEFAULT_SENDER})

def check_auth():
    if not AUTH_TOKEN: return True, None
    got = request.headers.get("Authorization", "")
    if got.startswith("Bearer "):
        token = got.split(" ", 1)[1].strip()
        if token == AUTH_TOKEN: return True, None
    return False, (jsonify({"ok": False, "error": "unauthorized"}), 401)

@app.post("/api/sms")
def sms_send():
    ok, err = check_auth()
    if not ok: return err
    try:
        payload = request.get_json(force=True) or {}
    except:
        payload = {}
    
    to = str(payload.get("to", "")).strip()
    from_num = str(payload.get("from", DEFAULT_SENDER)).strip() or DEFAULT_SENDER
    text = str(payload.get("text", "")).strip()
    dry = bool(payload.get("dry", False))

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
            out = {"ok": r.status_code < 300, "provider": "solapi", "response": r.json() if "json" in r.headers.get("Content-Type","") else r.text}
            return (json.dumps(out, ensure_ascii=False), r.status_code, {"Content-Type": "application/json"})
        except Exception as e:
            return jsonify({"ok": False, "error": "solapi-failed", "detail": str(e)}), 502

    return jsonify({"ok": True, "provider": "mock", "dry": True})

WEB_UI_HTML = r"""<!doctype html>
<html lang="ko"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>서울더함수학학원 문자 전송</title>
<style>
:root{--b:#cbd5e1;--text:#334155;--muted:#64748b;--bg:#f8fafc;--white:#fff;--brand:#2563eb;--accent:#0ea5e9}
*{box-sizing:border-box}
body{font-family:system-ui,-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif;background:var(--bg);margin:0;color:var(--text)}
.wrap{max-width:980px;margin:24px auto;padding:16px}
.card{background:var(--white);border:1px solid #e5e7eb;border-radius:12px;padding:16px;box-shadow:0 1px 2px rgba(0,0,0,.04)}
.controls{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(220px,1fr))}
.col{display:flex;flex-direction:column;gap:6px}
label{font-size:12px;font-weight:600;color:var(--muted)}
input,textarea{width:100%;padding:10px;border:1px solid var(--b);border-radius:10px;font-size:14px}
textarea{min-height:100px;font-family:inherit}
button{padding:10px 14px;border-radius:10px;border:1px solid var(--b);background:var(--white);cursor:pointer;font-size:14px}
button.primary{background:var(--brand);color:var(--white);border-color:var(--brand);font-weight:600}
.pill{padding:8px 12px;border-radius:999px;border:1px solid var(--b);background:var(--white);font-size:13px;cursor:pointer;white-space:nowrap}
.pill.on{background:var(--accent);color:var(--white);border-color:var(--accent)}
.grid{display:grid;gap:8px;grid-template-columns:repeat(auto-fill,minmax(110px,1fr))}
.templates{display:flex;flex-wrap:wrap;gap:8px}
.mt16{margin-top:16px}.mt8{margin-top:8px}
pre{background:#0b1020;color:#c7d2fe;padding:12px;border-radius:10px;overflow:auto;font-size:12px}
.status{font-size:13px;font-weight:600}
.actionbar{display:flex;align-items:center;gap:12px;margin-top:16px}
.inlinecheck{display:flex;align-items:center;gap:4px;cursor:pointer}
</style>
</head>
<body>
<div class="wrap">
  <h2>서울더함수학학원 문자 전송</h2>
  
  <div class="card">
    <div class="controls">
      <div class="col"><label>발신번호</label><input id="fromNum" disabled></div>
      <div class="col"><label>학생 검색</label><input id="search" placeholder="이름 검색..."></div>
    </div>
  </div>

  <div class="card mt16">
    <label>1. 선생님 선택</label>
    <div id="teacherBox" class="grid mt8"></div>
    <label class="mt16" style="display:block">2. 학생 선택</label>
    <div id="studentBox" class="grid mt8"></div>
  </div>

  <div class="card mt16">
    <label>3. 문구 및 수신설정</label>
    <div class="templates mt8" id="tpls"></div>
    
    <div class="mt16">
      <label>수신 대상</label>
      <div class="templates mt8">
        <span class="pill on" data-to="parent">학부모</span>
        <span class="pill" data-to="student">학생</span>
        <span class="pill" data-to="custom">직접입력</span>
      </div>
      <input id="customTo" class="mt8" placeholder="01012345678" style="display:none">
      <div class="mt8" style="font-size:13px">수신번호: <b id="toPreview">-</b></div>
    </div>

    <div class="mt16">
      <label>문자 내용 (수정 가능)</label>
      <textarea id="text"></textarea>
    </div>

    <div class="actionbar">
      <button id="send" class="primary">전송하기</button>
      <label class="inlinecheck"><input type="checkbox" id="dry"> <span class="muted">Dry-run (테스트)</span></label>
      <span id="status" class="status"></span>
    </div>

    <div class="mt16">
      <label>로그</label>
      <pre id="out">결과가 여기에 표시됩니다.</pre>
    </div>
  </div>
</div>

<script>
const ROSTER = {
  "장호민": [
    {"id": "장호민::전여진", "name": "전여진", "parentPhone": "01044179457", "studentPhone": "01044189457"},
    {"id": "장호민::신승현", "name": "신승현", "parentPhone": "01045340302", "studentPhone": "01027390302"},
    {"id": "장호민::허다희", "name": "허다희", "parentPhone": "01034413292", "studentPhone": "01021243292"},
    {"id": "장호민::조예은", "name": "조예은", "parentPhone": "01056074622", "studentPhone": ""},
    {"id": "장호민::최승리", "name": "최승리", "parentPhone": "01056715781", "studentPhone": "01036055781"},
    {"id": "장호민::이정원", "name": "이정원", "parentPhone": "01080087122", "studentPhone": "01055408535"},
    {"id": "장호민::김정윤", "name": "김정윤", "parentPhone": "01022077171", "studentPhone": ""},
    {"id": "장호민::조희주", "name": "조희주", "parentPhone": "01034338033", "studentPhone": ""},
    {"id": "장호민::정윤슬", "name": "정윤슬", "parentPhone": "01051050952", "studentPhone": ""},
    {"id": "장호민::김리우", "name": "김리우", "parentPhone": "01077214721", "studentPhone": ""},
    {"id": "장호민::최설아", "name": "최설아", "parentPhone": "01037686015", "studentPhone": ""},
    {"id": "장호민::전태식", "name": "전태식", "parentPhone": "01066073353", "studentPhone": ""},
    {"id": "장호민::박하은", "name": "박하은", "parentPhone": "01043084759", "studentPhone": ""},
    {"id": "장호민::김민균", "name": "김민균", "parentPhone": "01055068033", "studentPhone": ""},
    {"id": "장호민::박서윤", "name": "박서윤", "parentPhone": "01065333681", "studentPhone": ""},
    {"id": "장호민::전아인", "name": "전아인", "parentPhone": "01040040318", "studentPhone": ""},
    {"id": "장호민::이현은", "name": "이현은", "parentPhone": "01062651516", "studentPhone": ""},
    {"id": "장호민::하지우", "name": "하지우", "parentPhone": "01044217783", "studentPhone": ""},
    {"id": "장호민::이채라", "name": "이채라", "parentPhone": "", "studentPhone": ""},
    {"id": "장호민::옥범준", "name": "옥범준", "parentPhone": "01096733240", "studentPhone": ""},
    {"id": "장호민::조성훈", "name": "조성훈", "parentPhone": "01020714311", "studentPhone": ""},
    {"id": "장호민::오지연", "name": "오지연", "parentPhone": "01044192557", "studentPhone": ""},
    {"id": "장호민::임가은", "name": "임가은", "parentPhone": "01098489802", "studentPhone": ""},
    {"id": "장호민::김도원", "name": "김도원", "parentPhone": "01033386763", "studentPhone": ""},
    {"id": "장호민::권은유", "name": "권은유", "parentPhone": "01094115087", "studentPhone": ""},
    {"id": "장호민::강현준", "name": "강현준", "parentPhone": "01075672641", "studentPhone": ""},
    {"id": "장호민::이준근", "name": "이준근", "parentPhone": "01066245875", "studentPhone": ""},
    {"id": "장호민::송유민", "name": "송유민", "parentPhone": "01088081413", "studentPhone": ""},
    {"id": "장호민::이태우", "name": "이태우", "parentPhone": "01051773239", "studentPhone": ""},
    {"id": "장호민::이서윤", "name": "이서윤", "parentPhone": "01023552566", "studentPhone": ""},
    {"id": "장호민::김재운", "name": "김재운", "parentPhone": "01086701915", "studentPhone": ""},
    {"id": "장호민::김도연", "name": "김도연", "parentPhone": "01033386763", "studentPhone": ""},
    {"id": "장호민::정민우", "name": "정민우", "parentPhone": "01050531629", "studentPhone": ""}
  ],
  "이헌철": [
    {"id": "이헌철::고현빈", "name": "고현빈", "parentPhone": "", "studentPhone": ""},
    {"id": "이헌철::차은호", "name": "차은호", "parentPhone": "01095790135", "studentPhone": "01094003148"},
    {"id": "이헌철::최형준", "name": "최형준", "parentPhone": "01076517704", "studentPhone": ""},
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
    {"id": "이헌철::백소율", "name": "백소율", "parentPhone": "01099537571", "studentPhone": ""},
    {"id": "이헌철::신은재", "name": "신은재", "parentPhone": "01073810826", "studentPhone": ""},
    {"id": "이헌철::연정흠", "name": "연정흠", "parentPhone": "01054595704", "studentPhone": ""},
    {"id": "이헌철::유강민", "name": "유강민", "parentPhone": "01089309296", "studentPhone": ""},
    {"id": "이헌철::남이준", "name": "남이준", "parentPhone": "01049477172", "studentPhone": ""},
    {"id": "이헌철::이현", "name": "이현", "parentPhone": "01083448867", "studentPhone": ""},
    {"id": "이헌철::정유진", "name": "정유진", "parentPhone": "01033898056", "studentPhone": ""},
    {"id": "이헌철::전찬식", "name": "전찬식", "parentPhone": "01066073353", "studentPhone": ""},
    {"id": "이헌철::김주환", "name": "김주환", "parentPhone": "01037602796", "studentPhone": ""},
    {"id": "이헌철::김도윤", "name": "김도윤", "parentPhone": "01090952844", "studentPhone": ""},
    {"id": "이헌철::김도현", "name": "김도현", "parentPhone": "01044087732", "studentPhone": ""},
    {"id": "이헌철::이유근", "name": "이유근", "parentPhone": "01027106068", "studentPhone": ""},
    {"id": "이헌철::장민경", "name": "장민경", "parentPhone": "01066741973", "studentPhone": ""},
    {"id": "이헌철::홍가은", "name": "홍가은", "parentPhone": "01094178304", "studentPhone": ""},
    {"id": "이헌철::윤대철", "name": "윤대철", "parentPhone": "01091337052", "studentPhone": ""},
    {"id": "이헌철::김기범", "name": "김기범", "parentPhone": "01051881350", "studentPhone": ""},
    {"id": "이헌철::송유담", "name": "송유담", "parentPhone": "01093940117", "studentPhone": ""},
    {"id": "이헌철::장민아", "name": "장민아", "parentPhone": "01049404508", "studentPhone": ""},
    {"id": "이헌철::유재훈", "name": "유재훈", "parentPhone": "01033838321", "studentPhone": ""},
    {"id": "최윤영::조정운", "name": "조정운", "parentPhone": "01074321567", "studentPhone": ""},
  ],
  "최윤영": [
    {"id": "최윤영::안유진", "name": "안유진", "parentPhone": "01039113947", "studentPhone": ""},
    {"id": "최윤영::김류은", "name": "김류은", "parentPhone": "01049370692", "studentPhone": "01064880692"},
    {"id": "최윤영::진세헌", "name": "진세헌", "parentPhone": "01094233540", "studentPhone": "01093917471"},
    {"id": "최윤영::서동욱", "name": "서동욱", "parentPhone": "01089197997", "studentPhone": ""},
    {"id": "최윤영::기도윤", "name": "기도윤", "parentPhone": "01047612937", "studentPhone": "01057172937"},
    {"id": "최윤영::황세빈", "name": "황세빈", "parentPhone": "01029340929", "studentPhone": ""},
    {"id": "최윤영::최시원", "name": "최시원", "parentPhone": "01091925924", "studentPhone": ""},
    {"id": "최윤영::이동현", "name": "이동현", "parentPhone": "01095905486", "studentPhone": ""},
    {"id": "최윤영::이소영", "name": "이소영", "parentPhone": "01080253405", "studentPhone": ""},
    {"id": "최윤영::최현서", "name": "최현서", "parentPhone": "01026618590", "studentPhone": ""},
    {"id": "최윤영::신유나", "name": "신유나", "parentPhone": "01099245907", "studentPhone": ""},
    {"id": "최윤영::신유찬", "name": "신유찬", "parentPhone": "01099245907", "studentPhone": ""},
    {"id": "최윤영::노유종", "name": "노유종", "parentPhone": "01047626707", "studentPhone": ""},
    {"id": "최윤영::정다율", "name": "정다율", "parentPhone": "01050531629", "studentPhone": ""},
    {"id": "최윤영::최성현", "name": "최성현", "parentPhone": "01037465003", "studentPhone": ""},
    {"id": "최윤영::유하엘", "name": "유하엘", "parentPhone": "01035796389", "studentPhone": ""},
    {"id": "최윤영::이수빈", "name": "이수빈", "parentPhone": "01034725104", "studentPhone": "01088404945"},
    {"id": "최윤영::김범준", "name": "김범준", "parentPhone": "01036297472", "studentPhone": ""},
    {"id": "최윤영::김지환", "name": "김지환", "parentPhone": "01085822669", "studentPhone": ""},
    {"id": "최윤영::김강휘", "name": "김강휘", "parentPhone": "01091263383", "studentPhone": ""},
    {"id": "최윤영::이채은", "name": "이채은", "parentPhone": "01066394676", "studentPhone": ""},
    {"id": "최윤영::하유찬", "name": "하유찬", "parentPhone": "01075571627", "studentPhone": ""},
    {"id": "최윤영::안치현", "name": "안치현", "parentPhone": "01040227709", "studentPhone": ""},
    {"id": "최윤영::고결", "name": "고결", "parentPhone": "01036179299", "studentPhone": ""},
    {"id": "최윤영::이현범", "name": "이현범", "parentPhone": "01094312256", "studentPhone": ""},
    {"id": "최윤영::현가비", "name": "현가비", "parentPhone": "01094083490", "studentPhone": ""},
    {"id": "최윤영::정해수", "name": "정해수", "parentPhone": "01040782250", "studentPhone": ""},
    {"id": "최윤영::안지우", "name": "안지우", "parentPhone": "01034323651", "studentPhone": ""},
    {"id": "최윤영::범정우", "name": "범정우", "parentPhone": "01035988684", "studentPhone": ""}
  ],
  "황재선": [
    {"id": "황재선::강나경", "name": "강나경", "parentPhone": "01036502963", "studentPhone": "01059322963"},
    {"id": "황재선::변민경", "name": "변민경", "parentPhone": "01020067093", "studentPhone": "01079387093"},
    {"id": "황재선::박정우", "name": "박정우", "parentPhone": "01077381679", "studentPhone": ""},
    {"id": "황재선::안준혁", "name": "안준혁", "parentPhone": "01027459771", "studentPhone": ""},
    {"id": "황재선::강이현", "name": "강이현", "parentPhone": "01030522547", "studentPhone": ""},
    {"id": "황재선::장지후", "name": "장지후", "parentPhone": "01066741973", "studentPhone": ""},
    {"id": "황재선::권민결", "name": "권민결", "parentPhone": "01045723566", "studentPhone": ""},
    {"id": "황재선::임하준", "name": "임하준", "parentPhone": "01048557183", "studentPhone": ""},
    {"id": "황재선::안치운", "name": "안치운", "parentPhone": "01027440458", "studentPhone": ""},
    {"id": "황재선::김예준", "name": "김예준", "parentPhone": "01045876999", "studentPhone": ""},
    {"id": "황재선::고하은", "name": "고하은", "parentPhone": "01036245135", "studentPhone": ""},
    {"id": "황재선::신준화", "name": "신준화", "parentPhone": "01038382098", "studentPhone": ""},
    {"id": "황재선::송유현", "name": "송유현", "parentPhone": "01088081413", "studentPhone": ""},
    {"id": "황재선::이채영", "name": "이채영", "parentPhone": "01035201122", "studentPhone": ""}
  ]
};

const TEMPLATES = [
  { label:"미등원 안내", text:"안녕하세요. 서울더함수학학원입니다. {given} 아직 등원 하지 않았습니다." },
  { label:"조퇴 안내", text:"안녕하세요. 서울더함수학학원입니다. {given} 아파서 오늘 조퇴하였습니다. 아이 상태 확인해주세요." },
  { label:"숙제 미제출", text:"안녕하세요. 서울더함수학학원입니다. {given} 오늘 과제 미제출입니다. 가정에서 점검 부탁드립니다." },
  { label:"교재 공지", text:"안녕하세요. 서울더함수학학원입니다. {given} 새로운 교재 준비 부탁드립니다." }
];

const state = {
  currentTeacher: "",
  currentStudent: null,
  currentTemplate: TEMPLATES[0], // 현재 선택된 템플릿 원본 저장
  toType: "parent",
  defaultFrom: ""
};

const $ = sel => document.querySelector(sel);
const $$ = sel => Array.from(document.querySelectorAll(sel));

function givenName(full) {
  const s = String(full||"").trim();
  if (!s) return "";
  if (/^[가-힣]+$/.test(s) && s.length >= 2) return s.slice(1);
  return s;
}

function applyText() {
  const s = state.currentStudent;
  if(!s || !state.currentTemplate) return;
  // 템플릿 원본에서 {given}을 현재 학생의 이름으로 치환하여 textarea에 삽입
  const processed = state.currentTemplate.text.replaceAll("{given}", givenName(s.name));
  $("#text").value = processed;
  updatePreview();
}

function updatePreview() {
  const s = state.currentStudent;
  let toNum = "";
  if(state.toType === "parent") toNum = s?.parentPhone || "";
  else if(state.toType === "student") toNum = s?.studentPhone || "";
  else toNum = $("#customTo").value;
  
  $("#toPreview").textContent = toNum || "(번호 없음)";
}

function renderTeachers() {
  const box = $("#teacherBox"); box.innerHTML = "";
  Object.keys(ROSTER).forEach(t => {
    const b = document.createElement("button");
    b.className = "pill" + (t === state.currentTeacher ? " on" : "");
    b.textContent = t;
    b.onclick = () => {
      state.currentTeacher = t;
      state.currentStudent = ROSTER[t][0];
      renderTeachers(); renderStudents(); applyText();
    };
    box.appendChild(b);
  });
}

function renderStudents() {
  const box = $("#studentBox"); box.innerHTML = "";
  const list = ROSTER[state.currentTeacher] || [];
  const q = $("#search").value.trim();
  const filtered = q ? list.filter(s => s.name.includes(q)) : list;

  filtered.forEach(s => {
    const b = document.createElement("button");
    b.className = "pill" + (state.currentStudent?.id === s.id ? " on" : "");
    b.textContent = s.name;
    b.onclick = () => {
      state.currentStudent = s;
      renderStudents(); applyText();
    };
    box.appendChild(b);
  });
}

async function send() {
  const to = $("#toPreview").textContent.replace(/\D/g, "");
  const text = $("#text").value.trim();
  if(!to || !text) return alert("수신번호와 내용을 확인하세요.");

  $("#status").textContent = "전송 중...";
  try {
    const res = await fetch("/api/sms", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({to, text, from: state.defaultFrom, dry: $("#dry").checked})
    });
    const data = await res.json();
    $("#out").textContent = JSON.stringify(data, null, 2);
    $("#status").textContent = res.ok ? "성공" : "실패";
  } catch(e) {
    $("#status").textContent = "오류";
  }
}

// 초기화
(async () => {
  try {
    const res = await fetch("/api/sms/config");
    const cfg = await res.json();
    state.defaultFrom = cfg.defaultFrom;
    $("#fromNum").value = cfg.defaultFrom;
  } catch(e) {}

  // 템플릿 버튼 생성
  const tplBox = $("#tpls");
  TEMPLATES.forEach((t, idx) => {
    const b = document.createElement("button");
    b.className = "pill" + (idx === 0 ? " on" : "");
    b.textContent = t.label;
    b.onclick = () => {
      $$("#tpls .pill").forEach(btn => btn.classList.remove("on"));
      b.classList.add("on");
      state.currentTemplate = t;
      applyText();
    };
    tplBox.appendChild(b);
  });

  // 수신대상 버튼 이벤트
  $$("[data-to]").forEach(btn => {
    btn.onclick = () => {
      $$("[data-to]").forEach(b => b.classList.remove("on"));
      btn.classList.add("on");
      state.toType = btn.dataset.to;
      $("#customTo").style.display = state.toType === "custom" ? "block" : "none";
      updatePreview();
    };
  });

  state.currentTeacher = Object.keys(ROSTER)[0];
  state.currentStudent = ROSTER[state.currentTeacher][0];
  
  renderTeachers(); renderStudents(); applyText();

  $("#search").oninput = renderStudents;
  $("#customTo").oninput = updatePreview;
  $("#send").onclick = send;
})();
</script>
</body></html>
"""

@app.get("/ui")
def ui():
    return Response(WEB_UI_HTML, mimetype="text/html; charset=utf-8")

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=False)
