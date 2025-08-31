# -*- coding: utf-8 -*-
# Nurigo/Solapi SMS proxy (Flask)
# Endpoints:
#   GET  /                   -> health
#   GET  /routes             -> list routes (debug)
#   GET  /api/sms/config     -> {"provider": "...", "defaultFrom": "010..."}
#   POST /api/sms            -> {to, from, text, dry?}  (optional Auth via AUTH_TOKEN)
# Environment:
#   PORT            : port to bind (Render provides this)
#   DEFAULT_SENDER  : default "from" number (e.g., 01080348069)
#   SOLAPI_KEY      : Solapi key (optional if FORWARD_URL used)
#   SOLAPI_SECRET   : Solapi secret
#   FORWARD_URL     : if set, forward JSON to this URL instead of Solapi
#   AUTH_TOKEN      : if set, require header "Authorization: Bearer <AUTH_TOKEN>"

import os
import json
import base64
from datetime import datetime

from flask import Flask, request, jsonify
from flask_cors import CORS
import requests

app = Flask(__name__)
CORS(app)  # allow cross-origin for quick tests; tighten in prod

DEFAULT_SENDER = os.getenv("DEFAULT_SENDER", "").strip()
FORWARD_URL    = os.getenv("FORWARD_URL", "").strip()
SOLAPI_KEY     = os.getenv("SOLAPI_KEY", "").strip()
SOLAPI_SECRET  = os.getenv("SOLAPI_SECRET", "").strip()
AUTH_TOKEN     = os.getenv("AUTH_TOKEN", "").strip()

def provider():
    if FORWARD_URL:
        return "forward"
    if SOLAPI_KEY and SOLAPI_SECRET:
        return "solapi"
    return "mock"

@app.get("/")
def root():
    return {"ok": True, "service": "nurigo-sms-proxy", "provider": provider()}, 200

@app.get("/routes")
def routes():
    return {"routes":[{"rule":r.rule,"methods":sorted(list(r.methods))} for r in app.url_map.iter_rules()]}

@app.get("/api/sms/config")
def sms_config():
    return jsonify({"provider": provider(), "defaultFrom": DEFAULT_SENDER})

def _auth_ok():
    if not AUTH_TOKEN:
        return True, None
    got = request.headers.get("Authorization","")
    if got.startswith("Bearer "):
        tok = got.split(" ",1)[1].strip()
        if tok and tok == AUTH_TOKEN:
            return True, None
    return False, (jsonify({"ok": False, "error":"unauthorized"}), 401)

@app.post("/api/sms")
def sms_send():
    ok, err = _auth_ok()
    if not ok:
        return err

    try:
        payload = request.get_json(force=True) or {}
    except Exception:
        payload = {}
    to       = str(payload.get("to","")).strip()
    from_num = str(payload.get("from", DEFAULT_SENDER)).strip() or DEFAULT_SENDER
    text     = str(payload.get("text","")).strip()
    dry      = bool(payload.get("dry", False))

    if not to or not text:
        return jsonify({"ok": False, "error": "missing to/text"}), 400

    # Forward to existing server if configured
    if FORWARD_URL:
        try:
            r = requests.post(FORWARD_URL, json={"to":to,"from":from_num,"text":text,"dry":dry}, timeout=15)
            return (r.text, r.status_code, {"Content-Type": r.headers.get("Content-Type","application/json")})
        except Exception as e:
            return jsonify({"ok": False, "error": "forward-failed", "detail": str(e)}), 502

    # Direct call to Solapi (Messages v4)
    if SOLAPI_KEY and SOLAPI_SECRET and not dry:
        try:
            auth = base64.b64encode(f"{SOLAPI_KEY}:{SOLAPI_SECRET}".encode("utf-8")).decode("ascii")
            r = requests.post(
                "https://api.solapi.com/messages/v4/send",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Basic {auth}"
                },
                json={ "message": {"to": to, "from": from_num, "text": text} },
                timeout=15
            )
            ctype = r.headers.get("Content-Type","")
            data = r.json() if ctype.startswith("application/json") else {"raw": r.text}
            out  = {"ok": r.status_code < 300, "provider":"solapi", "response": data}
            return (json.dumps(out, ensure_ascii=False), r.status_code, {"Content-Type":"application/json"})
        except Exception as e:
            return jsonify({"ok": False, "error":"solapi-failed", "detail": str(e)}), 502

    # Mock (dry-run or no credentials)
    return jsonify({
        "ok": True, "provider":"mock", "dry": True,
        "echo": {"to": to, "from": from_num, "text": text, "len": len(text)},
        "at": datetime.utcnow().isoformat()+"Z"
    })

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    print("== URL MAP ==")
    print(app.url_map)
    app.run(host="0.0.0.0", port=port, debug=False)