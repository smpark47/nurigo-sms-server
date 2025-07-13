from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import time
import hmac
import hashlib
import base64
import os

app = Flask(__name__)
CORS(app)

API_KEY = "NCSQ4IUXA7HZXKZP"
API_SECRET = "Z32QAUC937DLGU82U92OUGUY75ZAIAGI"
FROM_NUMBER = "01080348069"
SOLAPI_URL = "/messages/v4/send"
BASE_URL = "https://api.solapi.com"

def make_signature(method, uri, timestamp, api_secret):
    data = f"{timestamp}{method.upper()}{uri}"
    hashed = hmac.new(api_secret.encode('utf-8'), data.encode('utf-8'), hashlib.sha256)
    return base64.b64encode(hashed.digest()).decode()

@app.route("/send-bulk", methods=["POST"])
def send_bulk():
    try:
        students = request.json
        results = []

        for s in students:
            text = "[서울더함수학학원]\n" + s["name"] + " 학생\n6월 월간보고\n기타 사항은 개별 확인 바랍니다."
            payload = {
                "message": {
                    "to": s["phone"],
                    "from": FROM_NUMBER,
                    "text": text
                }
            }

            timestamp = str(int(time.time() * 1000))
            signature = make_signature("POST", SOLAPI_URL, timestamp, API_SECRET)

            headers = {
                "Authorization": f"HMAC-SHA256 {API_KEY}:{signature}",
                "Content-Type": "application/json",
                "x-solapi-date": timestamp
            }

            res = requests.post(BASE_URL + SOLAPI_URL, json=payload, headers=headers)
            try:
                results.append({
                    "name": s["name"],
                    "phone": s["phone"],
                    "status": "success" if res.ok else "failed",
                    "result": res.json()
                })
            except:
                results.append({
                    "name": s["name"],
                    "phone": s["phone"],
                    "status": "failed",
                    "result": "no response"
                })

        return jsonify(results), 200

    except Exception as e:
        return jsonify({ "error": str(e) }), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
