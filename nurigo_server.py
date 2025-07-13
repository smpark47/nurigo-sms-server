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

        send_results = []
        for m in messages:
            to = m.get("phone") or m.get("to")
            text = m.get("text", "")
            name = m.get("name", "")

            if not to or not text:
                send_results.append({
                    "name": name,
                    "phone": to,
                    "status": "failed",
                    "result": {"message": "Missing phone or text"}
                })
                continue

            try:
                response = message_service.send_one({
                    "to": to,
                    "from": FROM_NUMBER,
                    "text": text
                })
                send_results.append({
                    "name": name,
                    "phone": to,
                    "status": "sent",
                    "result": {"message": response.get("message", "Sent")}
                })
            except Exception as e:
                send_results.append({
                    "name": name,
                    "phone": to,
                    "status": "failed",
                    "result": {"message": str(e)}
                })

        return jsonify(send_results), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
