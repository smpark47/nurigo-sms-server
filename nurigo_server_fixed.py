# --- (파일 상단에 이미 있음) ---
# from flask import Flask, request, jsonify
from flask import Response  # 추가

# --- (파일 하단, if __name__ == "__main__" 위쪽 아무 데나) ---
WEB_UI_HTML = r"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>SMS 테스트 (웹)</title>
  <style>
    body { font-family: system-ui,-apple-system,Segoe UI,Roboto,Apple SD Gothic Neo,Noto Sans KR,Arial,sans-serif; background:#f8fafc; margin:0; }
    .wrap { max-width: 840px; margin: 24px auto; padding: 16px; }
    .card { background:#fff; border:1px solid #e5e7eb; border-radius:12px; padding:16px; box-shadow:0 1px 2px rgba(0,0,0,.04); }
    .row { display:flex; gap:12px; flex-wrap:wrap; align-items:center; }
    .col { flex:1 1 240px; min-width:240px; }
    label { display:block; font-size:12px; color:#334155; margin-bottom:6px; }
    input, select, textarea { width:100%; padding:10px 12px; border:1px solid #cbd5e1; border-radius:10px; font-size:14px; }
    textarea { min-height: 120px; }
    button { padding:10px 14px; border-radius:10px; border:1px solid #cbd5e1; background:#fff; cursor:pointer; }
    button.primary { background:#2563eb; color:#fff; border-color:#2563eb; }
    .pill { padding:6px 10px; border-radius:999px; border:1px solid #cbd5e1; background:#fff; font-size:12px; cursor:pointer; }
    .pill.on { background:#0ea5e9; color:#fff; border-color:#0ea5e9; }
    .muted { color:#64748b; font-size:12px; }
    .grid { display:grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap:14px; }
    .templates { display:flex; flex-wrap:wrap; gap:8px; }
    .mt8{margin-top:8px}.mt12{margin-top:12px}.mt16{margin-top:16px}.mt24{margin-top:24px}
    pre { background:#0b1020; color:#c7d2fe; padding:12px; border-radius:10px; overflow:auto; }
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
            <input id="customTo" placeholder="직접 입력 (예: 01012345678)" style="display:none; flex:1 1 240px;">
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
    const STORAGE_KEY = "student_mgmt_standalone_v0_3_2";
    const TEMPLATES = [
      { label:"미등원 안내", text:"{name} 학생 아직 등원하지 않았습니다. 확인 부탁드립니다." },
      { label:"지각 안내",  text:"{name} 학생이 지각 중입니다. 10분 내 등원 예정인가요?" },
      { label:"조퇴 안내",  text:"{name} 학생 오늘 조퇴하였습니다. 귀가 시간 확인 부탁드립니다." },
      { label:"숙제 미제출", text:"{name} 학생 오늘 숙제 미제출입니다. 가정에서 점검 부탁드립니다." },
      { label:"수업 공지",  text:"{name} 학생 금일 수업 관련 안내드립니다: " }
    ];
    const onlyDigits = s => (s||"").replace(/\\D/g, "");
    const norm = s => {
      const d = onlyDigits(s);
      if (d.length===11) return d.replace(/(\\d{3})(\\d{4})(\\d{4})/, "$1-$2-$3");
      if (d.length===10) return d.replace(/(\\d{2,3})(\\d{3,4})(\\d{4})/, "$1-$2-$3");
      return s||"";
    };
    const $ = sel => document.querySelector(sel);
    const $$ = sel => Array.from(document.querySelectorAll(sel));
    const state = { list: [], toType: "parent", defaultFrom: "" };

    async function loadConfig() {
      try {
        const r = await fetch("/api/sms/config");
        if (!r.ok) throw new Error("bad config");
        const cfg = await r.json();
        state.defaultFrom = String(cfg.defaultFrom || "");
        $("#fromNum").value = state.defaultFrom || "(서버 미설정)";
        $("#cfgInfo").textContent = "provider: " + (cfg.provider || "unknown");
      } catch (e) {
        $("#cfgInfo").textContent = "서버 설정을 불러오지 못했습니다.";
      }
    }

    function loadStudents() {
      try {
        const raw = localStorage.getItem(STORAGE_KEY);
        if (raw) {
          const arr = JSON.parse(raw);
          if (Array.isArray(arr) && arr.length) state.list = arr;
        }
      } catch(e) {}
      if (!state.list.length) {
        state.list = [
          { id:"1", name:"김하늘", parentPhone:"01011112222", studentPhone:"01033334444" },
          { id:"2", name:"이도윤", parentPhone:"01055556666", studentPhone:"01077778888" },
          { id:"3", name:"박서연", parentPhone:"01099990000", studentPhone:"01012123434" }
        ];
      }
      const sel = $("#studentSel");
      sel.innerHTML = state.list.map(s => `<option value="${s.id}">${s.name}</option>`).join("");
    }

    function currentStudent() {
      const id = $("#studentSel").value;
      return state.list.find(x => x.id===id) || state.list[0];
    }

    function toNumber() {
      const s = currentStudent();
      if (state.toType==="parent") return norm(s?.parentPhone||"");
      if (state.toType==="student") return norm(s?.studentPhone||"");
      return norm($("#customTo").value||"");
    }

    function updatePreview() {
      const s = currentStudent();
      $("#toPreview").textContent = toNumber() || "-";
      const txt = $("#text").value || "";
      $("#preview").textContent = (txt||"").replaceAll("{name}", s?.name || "");
    }

    function setupToType() {
      $$(".pill").forEach(p => {
        p.addEventListener("click", () => {
          $$(".pill").forEach(x => x.classList.remove("on"));
          p.classList.add("on");
          state.toType = p.dataset.to;
          const isCustom = state.toType==="custom";
          $("#customTo").style.display = isCustom ? "block" : "none";
          updatePreview();
        });
      });
    }

    function setupTemplates() {
      const box = $("#tpls");
      box.innerHTML = "";
      TEMPLATES.forEach(t => {
        const b = document.createElement("button");
        b.className = "pill";
        b.textContent = t.label;
        b.addEventListener("click", () => {
          const name = currentStudent()?.name || "";
          $("#text").value = t.text.replaceAll("{name}", name);
          updatePreview();
        });
        box.appendChild(b);
      });
    }

    async function send() {
      const from = onlyDigits(state.defaultFrom || "");  // 서버 기본값 사용
      const to = onlyDigits(toNumber());
      const s = currentStudent();
      const text = ($("#text").value || "").replaceAll("{name}", s?.name || "");
      const dry = $("#dry").checked;

      $("#status").textContent = "전송 중...";
      if (!to) { alert("수신 번호가 비어있습니다."); $("#status").textContent=""; return; }
      if (!text.trim()) { alert("문자 내용을 입력하세요."); $("#status").textContent=""; return; }

      const payload = { to, from, text, student: s?.name, dry };
      try {
        const r = await fetch("/api/sms", { method:"POST", headers:{ "Content-Type":"application/json" }, body: JSON.stringify(payload) });
        const data = await r.json().catch(()=>({ok:false, status:r.status}));
        $("#out").textContent = JSON.stringify(data, null, 2);
        $("#status").textContent = r.ok ? "전송 요청 완료" : "전송 실패";
      } catch (e) {
        $("#out").textContent = String(e);
        $("#status").textContent = "오류";
      }
    }

    // init
    loadConfig();
    loadStudents();
    setupToType();
    setupTemplates();
    updatePreview();
    $("#studentSel").addEventListener("change", updatePreview);
    $("#customTo").addEventListener("input", updatePreview);
    $("#text").addEventListener("input", updatePreview);
    $("#send").addEventListener("click", send);
  </script>
</body>
</html>
"""

@app.get("/ui")
def ui():
    return Response(WEB_UI_HTML, mimetype="text/html; charset=utf-8")
