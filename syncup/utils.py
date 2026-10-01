"""Small helpers for the syncup app. Telegram lives in mobileapi/telegram.py (DB-configured bot)."""
import base64
import json

# =============== JSON Encoding/Decoding ===============
def encode_via_json(data):
    json_str = json.dumps(data)
    json_bytes = json_str.encode("utf-8")
    encoded = base64.urlsafe_b64encode(json_bytes).decode("utf-8")
    return encoded

def decode_via_json(encoded):
    json_bytes = base64.urlsafe_b64decode(encoded)
    json_str = json_bytes.decode("utf-8")
    data = json.loads(json_str)
    return data
