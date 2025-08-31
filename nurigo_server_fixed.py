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

    # 1) Forwarding to existing HTTP SMS service (only when not dry)
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
<title>SMS 테스트 (웹: 선생님/담당학생 빠른 발송)</title>
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
.mt4{margin-top:4px}.mt8{margin-top:8px}.mt12{margin-top:12px}.mt16{margin-top:16px}.mt24{margin-top:24px}
pre{background:#0b1020;color:#c7d2fe;padding:12px;border-radius:10px;overflow:auto}
.badge{font-size:11px;background:#eef2ff;color:#3730a3;padding:2px 6px;border-radius:999px;margin-left:6px;border:1px solid #c7d2fe}
h3{margin:0 0 8px 0;font-size:16px}
hr{border:none;height:1px;background:#e5e7eb;margin:14px 0}
</style>
</head>
<body>
<div class="wrap">
  <h2>문자 발송(웹) · 선생님/담당학생 원클릭</h2>
  <p class="muted">같은 오리진의 <code>/api/sms</code>, <code>/api/sms/config</code>를 호출합니다. CSV를 올려 선생님별 담당학생을 불러오세요.</p>

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
        <div class="muted mt8">서버에 AUTH_TOKEN이 설정된 경우, 발송 시 Authorization 헤더를 자동 첨부합니다.</div>
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

  <!-- CSV 업로드 & 선생님/학생 선택 -->
  <div class="card mt16">
    <h3>1) 선생님/담당학생 불러오기</h3>
    <div class="row">
      <div class="col">
        <label>CSV 업로드</label>
        <input type="file" id="csv" accept=".csv,text/csv">
        <div class="muted mt8">헤더 예시: <b>담당선생</b>, <b>학생이름</b>, <b>학부모전화</b>, <b>학생전화</b> (다른 표기도 자동 인식)</div>
      </div>
      <div class="col">
        <label>저장/불러오기</label>
        <div class="row">
          <button id="saveRoster">로컬 저장</button>
          <button id="loadRoster">로컬 불러오기</button>
          <button id="clearRoster">로컬 초기화</button>
        </div>
        <div class="muted mt8">브라우저 localStorage에 저장/불러옵니다.</div>
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
      <div class="muted mt8">학생 버튼을 클릭하면 수신번호가 자동 선택됩니다.</div>
    </div>
  </div>

  <!-- 수신대상/문구/발송 -->
  <div class="card mt16">
    <h3>2) 대상/문구 선택 → 발송</h3>
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
    <div class="mt16">
      <label>결과</label>
      <pre id="out">(아직 없음)</pre>
    </div>
  </div>
</div>

<script>
const STORAGE_KEY_ROSTER = "sms_ui_roster_v1";
const STORAGE_KEY_LAST   = "sms_ui_last_teacher_v1";
const TEMPLATES = [
  { label:"미등원 안내", text:"{name} 학생 아직 등원하지 않았습니다. 확인 부탁드립니다." },
  { label:"지각 안내",  text:"{name} 학생이 지각 중입니다. 10분 내 등원 예정인가요?" },
  { label:"조퇴 안내",  text:"{name} 학생 오늘 조퇴하였습니다. 귀가 시간 확인 부탁드립니다." },
  { label:"숙제 미제출", text:"{name} 학생 오늘 숙제 미제출입니다. 가정에서 점검 부탁드립니다." },
  { label:"수업 공지",  text:"{name} 학생 금일 수업 관련 안내드립니다: " }
];
const teacherColKeys = ["담당","담당선생","담당선생님","선생님","teacher","tch","담당자"];
const nameColKeys    = ["학생이름","이름","name","student","학생","성명"];
const parentColKeys  = ["학부모전화","학부모연락처","부모전화","보호자전화","보호자연락처","parent","parentphone"];
const studentColKeys = ["학생전화","연락처","student","studentphone","전화번호","핸드폰","휴대폰","mobile","cell"];
const onlyDigits = s => (s||"").replace(/\\D/g, "");
const norm = s => { const d=onlyDigits(s); if(d.length===11) return d.replace(/(\\d{3})(\\d{4})(\\d{4})/,"$1-$2-$3"); if(d.length===10) return d.replace(/(\\d{2,3})(\\d{3,4})(\\d{4})/,"$1-$2-$3"); return s||""; };
const $  = sel => document.querySelector(sel);
const $$ = sel => Array.from(document.querySelectorAll(sel));
const state = {
  provider:"", defaultFrom:"", token:"",
  roster: {},        // { teacher: [ {id,name,parentPhone,studentPhone} ] }
  teacherList: [],   // ["김T", "이T", ...]
  currentTeacher: "",
  currentStudent: null,
  toType: "parent",
  filteredStudents: []
};

async function loadConfig() {
  try{
    const r = await fetch("/api/sms/config");
    const cfg = await r.json();
    state.provider   = cfg.provider || "";
    state.defaultFrom= String(cfg.defaultFrom||"");
    $("#fromNum").value = state.defaultFrom || "(서버 미설정)";
    $("#cfgInfo").textContent = `provider: ${state.provider || "unknown"}`;
  }catch(e){
    $("#cfgInfo").textContent = "서버 설정을 불러오지 못했습니다.";
  }
}
function setupTemplates(){
  const box=$("#tpls"); box.innerHTML="";
  TEMPLATES.forEach(t=>{
    const b=document.createElement("button");
    b.className="pill";
    b.textContent=t.label;
    b.addEventListener("click",()=>{
      if(state.currentStudent){
        $("#text").value = t.text.replaceAll("{name}", state.currentStudent.name||"");
      }else{
        $("#text").value = t.text;
      }
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
      const isCustom = state.toType==="custom";
      $("#customTo").style.display = isCustom ? "block" : "none";
      updatePreview();
    });
  });
  $("#customTo").addEventListener("input", updatePreview);
}
function updatePreview(){
  const s = state.currentStudent;
  const to = computeTo();
  $("#toPreview").textContent = to || "-";
  const txt = $("#text").value || "";
  $("#preview").textContent = (txt||"").replaceAll("{name}", s?.name || "");
}
function computeTo(){
  if(state.toType==="custom") return norm($("#customTo").value||"");
  const s = state.currentStudent;
  if(!s) return "";
  if(state.toType==="parent")  return norm(s.parentPhone||"");
  if(state.toType==="student") return norm(s.studentPhone||"");
  return "";
}
function keyify(h){ return String(h||"").toLowerCase().replace(/\\s+/g,"").replace(/[^\\w가-힣]/g,""); }

function parseCSV(text){
  // 간단 CSV 파서 (따옴표 지원)
  const rows=[]; let row=[], cur="", inQ=false;
  for(let i=0;i<text.length;i++){
    const ch=text[i], nxt=text[i+1];
    if(inQ){
      if(ch==='"' && nxt==='"'){ cur+='"'; i++; }
      else if(ch==='"'){ inQ=false; }
      else cur+=ch;
    }else{
      if(ch==='"'){ inQ=true; }
      else if(ch===','){ row.push(cur); cur=""; }
      else if(ch==='\\n'){ row.push(cur); rows.push(row); row=[]; cur=""; }
      else if(ch==='\\r'){ /* skip */ }
      else cur+=ch;
    }
  }
  if(cur.length>0 || row.length>0){ row.push(cur); rows.push(row); }
  return rows;
}
function detectColumns(headers){
  const idx={};
  const H=headers.map(keyify);
  idx.teacher = (()=>{ const cand=["담당","담당선생","담당선생님","선생님","teacher","tch","담당자"]; for(let i=0;i<H.length;i++){ if(cand.includes(H[i])) return i;} return -1; })();
  idx.name    = (()=>{ const cand=["학생이름","이름","name","student","학생","성명"]; for(let i=0;i<H.length;i++){ if(cand.includes(H[i])) return i;} return -1; })();
  idx.parent  = (()=>{ const cand=["학부모전화","학부모연락처","부모전화","보호자전화","보호자연락처","parent","parentphone"]; for(let i=0;i<H.length;i++){ if(cand.includes(H[i])) return i;} return -1; })();
  idx.student = (()=>{ const cand=["학생전화","연락처","student","studentphone","전화번호","핸드폰","휴대폰","mobile","cell"]; for(let i=0;i<H.length;i++){ if(cand.includes(H[i])) return i;} return -1; })();
  return idx;
}
function buildRoster(rows){
  if(!rows.length) return {roster:{}, teachers:[]};
  const headers = rows[0];
  const idx = detectColumns(headers);
  if(idx.teacher<0 || idx.name<0){
    alert("CSV 헤더를 인식하지 못했습니다. 최소한 '담당선생'과 '학생이름' 열이 있어야 합니다.");
    return {roster:{}, teachers:[]};
  }
  const roster={}, teachersSet=new Set();
  for(let r=1;r<rows.length;r++){
    const cols = rows[r]; if(!cols || cols.length<2) continue;
    const teacher = String(cols[idx.teacher]||"").trim(); if(!teacher) continue;
    const name    = String(cols[idx.name]||"").trim();    if(!name) continue;
    const parent  = idx.parent>=0  ? onlyDigits(String(cols[idx.parent]||""))   : "";
    const student = idx.student>=0 ? onlyDigits(String(cols[idx.student]||""))  : "";
    const obj = { id: `${teacher}::${name}::${r}`, name, parentPhone: parent, studentPhone: student };
    if(!roster[teacher]) roster[teacher]=[];
    roster[teacher].append(obj);
    teachersSet.add(teacher);
  }
  const teachers=[...teachersSet].sort((a,b)=>a.localeCompare(b,"ko"));
  for(const t of teachers){ roster[t].sort((a,b)=>a.name.localeCompare(b.name,"ko")); }
  return {roster, teachers};
}

function renderTeachers(){
  const box=$("#teacherBox"); box.innerHTML="";
  if(!state.teacherList.length){ box.innerHTML='<span class="muted">선생님 데이터가 없습니다. CSV를 업로드하세요.</span>'; return; }
  state.teacherList.forEach(t=>{
    const b=document.createElement("button");
    b.className="pill"+(t===state.currentTeacher?" on":"");
    const cnt = (state.roster[t]||[]).length;
    b.innerHTML = `${t}<span class="badge">${cnt}</span>`;
    b.addEventListener("click",()=>{
      state.currentTeacher = t;
      localStorage.setItem(STORAGE_KEY_LAST, t);
      renderTeachers();
      renderStudents();
    });
    box.appendChild(b);
  });
}
function renderStudents(){
  const box=$("#studentBox"); box.innerHTML="";
  const list = (state.roster[state.currentTeacher]||[]);
  const q = ($("#search").value||"").trim();
  state.filteredStudents = q ? list.filter(s=>s.name.includes(q)) : list;
  if(!state.filteredStudents.length){
    box.innerHTML='<span class="muted">학생이 없습니다.</span>';
    state.currentStudent=null; updatePreview(); return;
  }
  state.filteredStudents.forEach(s=>{
    const b=document.createElement("button");
    b.className="pill"+(state.currentStudent && state.currentStudent.id===s.id ? " on":"");
    const phone = norm(s.parentPhone) || norm(s.studentPhone) || "-";
    b.innerHTML = `${s.name}<span class="badge">${phone}</span>`;
    b.addEventListener("click",()=>{
      state.currentStudent = s;
      updatePreview();
      if(!$("#text").value.trim()){
        const t=TEMPLATES[0];
        $("#text").value = t.text.replaceAll("{name}", s.name);
        updatePreview();
      }
      renderStudents();
    });
    box.appendChild(b);
  });
}

function saveRoster(){
  const data = { roster: state.roster, teacherList: state.teacherList, currentTeacher: state.currentTeacher };
  localStorage.setItem(STORAGE_KEY_ROSTER, JSON.stringify(data));
  alert("저장 완료 (localStorage)");
}
function loadRoster(){
  try{
    const raw=localStorage.getItem(STORAGE_KEY_ROSTER);
    if(!raw) { alert("저장된 데이터가 없습니다."); return; }
    const data=JSON.parse(raw);
    state.roster=data.roster||{};
    state.teacherList=data.teacherList||Object.keys(state.roster);
    state.currentTeacher=data.currentTeacher||state.teacherList[0]||"";
    renderTeachers(); renderStudents(); updatePreview();
  }catch(e){ alert("불러오기 실패: "+e); }
}
function clearRoster(){
  localStorage.removeItem(STORAGE_KEY_ROSTER);
  localStorage.removeItem(STORAGE_KEY_LAST);
  alert("로컬 저장소를 초기화했습니다.");
}

function hookCSV(){
  $("#csv").addEventListener("change", (ev)=>{
    const f = ev.target.files?.[0];
    if(!f) return;
    const fr=new FileReader();
    fr.onload = ()=>{
      const text=String(fr.result||"");
      const rows=parseCSV(text);
      const built=buildRoster(rows);
      state.roster=built.roster;
      state.teacherList=built.teachers;
      const last=localStorage.getItem(STORAGE_KEY_LAST);
      state.currentTeacher = (last && state.roster[last]) ? last : (state.teacherList[0]||"");
      renderTeachers(); renderStudents(); updatePreview();
    };
    fr.readAsText(f,"utf-8");
  });
  $("#search").addEventListener("input", renderStudents);
  $("#saveRoster").addEventListener("click", saveRoster);
  $("#loadRoster").addEventListener("click", loadRoster);
  $("#clearRoster").addEventListener("click", clearRoster);
}

// CSV utils
function keyify(h){ return String(h||"").toLowerCase().replace(/\\s+/g,"").replace(/[^\\w가-힣]/g,""); }
function parseCSV(text){
  const rows=[]; let row=[], cur="", inQ=false;
  for(let i=0;i<text.length;i++){
    const ch=text[i], nxt=text[i+1];
    if(inQ){
      if(ch==='"' && nxt==='"'){ cur+='"'; i++; }
      else if(ch==='"'){ inQ=false; }
      else cur+=ch;
    }else{
      if(ch==='"'){ inQ=true; }
      else if(ch===','){ row.push(cur); cur=""; }
      else if(ch==='\\n'){ row.push(cur); rows.push(row); row=[]; cur=""; }
      else if(ch==='\\r'){ /* skip */ }
      else cur+=ch;
    }
  }
  if(cur.length>0 || row.length>0){ row.push(cur); rows.push(row); }
  return rows;
}
function detectColumns(headers){
  const idx={};
  const H=headers.map(keyify);
  const find=(cands)=>{ for(let i=0;i<H.length;i++){ if(cands.includes(H[i])) return i; } return -1; };
  idx.teacher = find(["담당","담당선생","담당선생님","선생님","teacher","tch","담당자"]);
  idx.name    = find(["학생이름","이름","name","student","학생","성명"]);
  idx.parent  = find(["학부모전화","학부모연락처","부모전화","보호자전화","보호자연락처","parent","parentphone"]);
  idx.student = find(["학생전화","연락처","student","studentphone","전화번호","핸드폰","휴대폰","mobile","cell"]);
  return idx;
}
function buildRoster(rows){
  if(!rows.length) return {roster:{}, teachers:[]};
  const headers = rows[0];
  const idx = detectColumns(headers);
  if(idx.teacher<0 || idx.name<0){
    alert("CSV 헤더를 인식하지 못했습니다. 최소한 '담당선생'과 '학생이름' 열이 있어야 합니다.");
    return {roster:{}, teachers:[]};
  }
  const roster={}, teachersSet=new Set();
  for(let r=1;r<rows.length;r++){
    const cols = rows[r]; if(!cols || cols.length<2) continue;
    const teacher = String(cols[idx.teacher]||"").trim(); if(!teacher) continue;
    const name    = String(cols[idx.name]||"").trim();    if(!name) continue;
    const parent  = idx.parent>=0  ? onlyDigits(String(cols[idx.parent]||""))   : "";
    const student = idx.student>=0 ? onlyDigits(String(cols[idx.student]||""))  : "";
    const obj = { id: `${teacher}::${name}::${r}`, name, parentPhone: parent, studentPhone: student };
    if(!roster[teacher]) roster[teacher]=[];
    roster[teacher].push(obj);
    teachersSet.add(teacher);
  }
  const teachers=[...teachersSet].sort((a,b)=>a.localeCompare(b,"ko"));
  for(const t of teachers){ roster[t].sort((a,b)=>a.name.localeCompare(b.name,"ko")); }
  return {roster, teachers};
}

// 발송
async function send(){
  const token = ($("#token").value||"").trim();
  const headers = { "Content-Type":"application/json" };
  if(token) headers["Authorization"] = "Bearer "+token;

  const to   = onlyDigits(computeTo());
  const from = onlyDigits(state.defaultFrom||"");
  const s    = state.currentStudent;
  const text = ($("#text").value||"").replaceAll("{name}", s?.name||"");
  const dry  = $("#dry").checked;

  $("#status").textContent = "전송 중...";
  if(!s){ alert("학생을 먼저 선택하세요."); $("#status").textContent=""; return; }
  if(!to){ alert("수신 번호가 비어있습니다."); $("#status").textContent=""; return; }
  if(!text.trim()){ alert("문자 내용을 입력하세요."); $("#status").textContent=""; return; }

  const payload = { to, from, text, student: s.name, dry };
  try{
    const r = await fetch("/api/sms", { method:"POST", headers, body: JSON.stringify(payload) });
    const data = await r.json().catch(()=>({ok:false,status:r.status}));
    $("#out").textContent = JSON.stringify(data, null, 2);
    $("#status").textContent = r.ok ? (dry ? "드라이런 완료" : "전송 요청 완료") : "전송 실패";
  }catch(e){
    $("#out").textContent = String(e);
    $("#status").textContent = "오류";
  }
}

// init
(async function(){
  await loadConfig();
  setupTemplates();
  setupToType();
  hookCSV();
  const last=localStorage.getItem(STORAGE_KEY_LAST);
  if(last) state.currentTeacher=last;
  renderTeachers(); renderStudents(); updatePreview();
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
