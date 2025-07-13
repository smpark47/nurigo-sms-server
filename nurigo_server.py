from flask import Flask, request, jsonify
from flask_cors import CORS
import os
from solapi import SolapiMessageService

app = Flask(__name__)
CORS(app)

# Solapi 인증 정보
API_KEY = "NCSQ4IUXA7HZXKZP"
API_SECRET = "Z32QAUC937DLGU82U92OUGUY75ZAIAGI"
FROM_NUMBER = "01080348069"

message_service = SolapiMessageService(api_key=API_KEY, api_secret=API_SECRET)

@app.route("/send-bulk", methods=["POST"])
def send_bulk():
    try:
        data = request.get_json()
        messages = data.get("messages", [])

        if not messages:
            return jsonify({"error": "No messages to send"}), 400

        # 필수 필드만 추출하여 Solapi에 넘기기
        solapi_messages = []
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

            solapi_messages.append({
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

        if not solapi_messages:
            return jsonify(results_map), 400

        # Solapi로 실제 전송
        response = message_service.send_many({
            "messages": solapi_messages
        })

        return jsonify(results_map), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
