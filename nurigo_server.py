
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import os
import base64

app = Flask(__name__)
CORS(app)

NURIGO_API_URL = "https://api.coolsms.co.kr/messages/v4/send-many"
API_KEY = "NCSQ4IUXA7HZXKZP"
API_SECRET = "Z32QAUC937DLGU82U92OUGUY75ZAIAGI"
FROM_NUMBER = "01080348069"

@app.route("/send-bulk", methods=["POST"])
def send_bulk():
    try:
        students = request.json
        print("📨 수신한 요청 데이터:", students)

        messages = []
        for s in students:
            messages.append({
                "to": s["phone"],
                "from": FROM_NUMBER,
                "text": f"[서울더함수학학원]\n{s['name']} 학생\n6월 월간보고\n{format_message(s)}"
            })

        payload = { "messages": messages }

        # Basic 인증 헤더 생성
        auth_string = f"{API_KEY}:{API_SECRET}"
        auth_bytes = auth_string.encode("utf-8")
        auth_base64 = base64.b64encode(auth_bytes).decode("utf-8")
        headers = {
            "Authorization": f"Basic {auth_base64}",
            "Content-Type": "application/json"
        }

        res = requests.post(NURIGO_API_URL, json=payload, headers=headers)
        print("📬 Nurigo 응답:", res.status_code, res.text)

        return jsonify(res.json()), res.status_code

    except Exception as e:
        print("❌ 서버 오류:", str(e))
        return jsonify({ "error": str(e) }), 500

def format_message(s):
    return f"""
진도: {s.get('subject', '')} {s.get('chapter', '')}
성실도: {s.get('diligence', '')}/10
진도 소화도: {s.get('progress', '')}/10
이해도: {s.get('focus', '')}/10
기본: {s.get('basic', '')}/10, 중간: {s.get('intermediate', '')}/10, 심화: {s.get('advanced', '')}/10
특이사항: {s.get('specialNotes', '')}
""".strip()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
