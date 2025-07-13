from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import os

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

SOLAPI_API_URL = "https://api.solapi.com/messages/v4/send-many"
API_KEY = "NCSQ4IUXA7HZXKZP"
API_SECRET = "Z32QAUC937DLGU82U92OUGUY75ZAIAGI"
FROM_NUMBER = "01080348069"

@app.route("/send-bulk", methods=["POST"])
def send_bulk():
    try:
        students = request.json
        messages = []
        for s in students:
            text = "[서울더함수학학원]\n" + s["name"] + " 학생\n6월 월간보고\n기타 사항은 개별 확인 바랍니다."
            messages.append({
                "to": s["phone"],
                "from": FROM_NUMBER,
                "text": text
            })

        payload = { "messages": messages }

        headers = {
            "Authorization": API_KEY,
            "Content-Type": "application/json"
        }

        res = requests.post(SOLAPI_API_URL, json=payload, headers=headers)
        return jsonify(res.json()), res.status_code

    except Exception as e:
        return jsonify({ "error": str(e) }), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)