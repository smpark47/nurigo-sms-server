from flask import Flask, request, jsonify
from flask_cors import CORS
from solapi import SolapiMessageService
import os

app = Flask(__name__)
CORS(app)

API_KEY = "NCSQ4IUXA7HZXKZP"
API_SECRET = "Z32QAUC937DLGU82U92OUGUY75ZAIAGI"
FROM_NUMBER = "01080348069"

message_service = SolapiMessageService(api_key=API_KEY, api_secret=API_SECRET)

@app.route("/send-bulk", methods=["POST"])
def send_bulk():
    try:
        students = request.json
        results = []

        for s in students:
            msg = {
                "to": s["phone"],
                "from": FROM_NUMBER,
                "text": f"[서울더함수학학원]\n{s['name']} 학생\n6월 월간보고입니다.\n기타 사항은 개별 문의 바랍니다."
            }
            try:
                response = message_service.send(msg)
                results.append({
                    "name": s["name"],
                    "phone": s["phone"],
                    "status": "success",
                    "result": response
                })
            except Exception as e:
                results.append({
                    "name": s["name"],
                    "phone": s["phone"],
                    "status": "failed",
                    "result": str(e)
                })

        return jsonify(results), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
