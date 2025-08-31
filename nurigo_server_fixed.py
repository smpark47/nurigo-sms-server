# -*- coding: utf-8 -*-
"""
Nurigo/Solapi SMS proxy (Flask)

Endpoints
  GET  /                   -> health
  GET  /routes             -> list routes (debug)
  GET  /api/sms/config     -> {"provider": "...", "defaultFrom": "010..."}
  POST /api/sms            -> {to, from, text, dry?}

Env Vars
  PORT            : bind port (Render sets this automatically)
  DEFAULT_SENDER  : default "from" number (e.g., 01080348069)
  SOLAPI_KEY      : Solapi API key (use if not forwarding)
  SOLAPI_SECRET   : Solapi API secret
  FORWARD_URL     : if set, forward JSON to this URL instead of calling Solapi
  AUTH_TOKEN      : if set, require header "Authorization: Bearer <AUTH_TOKEN>"
"""

import os
import json
import hmac
import hashlib
import secrets
import requests
from datetime import datetime, timezone

from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)  # tighten allowed origins in production if needed

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
    """Optional bearer gate to prevent open relay."""
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

    # 1) Forwarding to an existing HTTP SMS service
    if FORWARD_URL:
        try:
            r = requests.post(
                FORWARD_URL,
                json={"to": to, "from": from_num, "text": text},
                timeout=15,
            )
            # relay as-is
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
            # Solapi Authorization (HMAC-SHA256) : signature = HMAC_SHA256(secret, date + salt)
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
                headers={
                    "Content-Type": "application/json",
                    "Authorization": auth_header,
                },
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


@app.get("/favicon.ico")
def _favicon():
    # avoid 404 noise
    return ("", 204)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    print("== URL MAP ==")
    print(app.url_map)
    app.run(host="0.0.0.0", port=port, debug=False)
