from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import requests
import time
import hmac
import hashlib
import base64

app = Flask(__name__)
CORS(app)

print("== URL MAP ==")
print(app.url_map)

API_KEY = "NCSQ4IUXA7HZXKZP"
API_SECRET = "Z32QAUC937DLGU82U92OUGUY75ZAIAGI"
FROM_NUMBER = "01080348069"
SOLAPI_URI = "/messages/v4/send-many"
SOLAPI_URL = "https://api.solapi.com" + SOLAPI_URI

def make_signature(api_key, api_secret, method, uri, timestamp):
    data = f"{timestamp}{method.upper()}{uri}"
    hashed = hmac.new(api_secret.encode("utf-8"), data.encode("utf-8"), hashlib.sha256)
    return base64.b64encode(hashed.digest()).decode()

@app.route("/send-bulk", methods=["POST"])
def send_bulk():
    try:
        data = request.get_json()
        messages = data.get("messages", [])

        if not messages:
            return jsonify({"error": "No messages to send"}), 400

        # 메시지 유효성 검사 및 변환
        formatted = []
        results_map = []
        for msg in messages:
            to = msg.get("phone") or msg.get("to")
            text = msg.get("text", "")
            name = msg.get("name", "")

            if not to or not text:
                results_map.append({
                    "name": name,
                    "phone": to,
                    "status": "failed",
                    "result": {"message": "Missing phone or text"}
                })
                continue

            formatted.append({
                "to": to,
                "from": FROM_NUMBER,
                "text": text
            })
            results_map.append({
                "name": name,
                "phone": to,
                "status": "queued",
                "result": {"message": "전송 시도 중"}
            })

        if not formatted:
            return jsonify(results_map), 400

        timestamp = str(int(time.time() * 1000))
        signature = make_signature(API_KEY, API_SECRET, "POST", SOLAPI_URI, timestamp)

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"HMAC {API_KEY}:{signature}",
            "Timestamp": timestamp
        }

        res = requests.post(SOLAPI_URL, json={"messages": formatted}, headers=headers)
        res.raise_for_status()
        return jsonify(results_map), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
