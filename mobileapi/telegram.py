"""Telegram relay for the mobile side.

Our single bot (token stored in AppConfig, not env — editable on the admin Telegram page) sends
one-way reports/messages to chat ids the partner supplies. The partner adds our bot to their group
or the person starts our bot; then they send us that chat id via the Partner API. Send-only — no
inbound webhook, no OTP/interactive prompts here.

Reuses the retry + MarkdownV2->plain fallback approach proven in syncup/utils.py, but addressed by a
RAW chat_id (not a hard-coded group index) and configured from the database.
"""
import logging
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .models import AppConfig

logger = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"
MAX_TEXT = 4096  # Telegram's hard limit for a message body.

# Retry policy for send(): up to 3 attempts total on TRANSIENT failures only.
MAX_SEND_ATTEMPTS = 3
_RETRYABLE_CODES = {420, 429, 500, 502, 503, 504}  # flood/rate-limit + server-side
_BACKOFF = [0.5, 1.5]      # seconds slept before the 2nd and 3rd attempts
_MAX_SLEEP = 3.0           # cap, so a big Telegram retry_after never stalls the request inline


def _token():
    return (AppConfig.load().telegram_bot_token or "").strip()


def is_configured():
    return bool(_token())


def _session():
    # Connection/read-level retries only. Status-based retries are owned by send()'s loop so the
    # "try 3 times" budget stays explicit and isn't multiplied by a second retry layer.
    retry = Retry(total=2, connect=2, read=2, backoff_factor=0.5, status=0, allowed_methods=None)
    s = requests.Session()
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


def _call(method, payload, http="post", token=None, timeout=4.0):
    """Low-level Bot API call. Returns the parsed JSON dict, or None on transport failure."""
    tok = (token or _token())
    if not tok:
        return None
    url = API.format(token=tok, method=method)
    try:
        with _session() as s:
            resp = s.request(http, url, json=payload, timeout=timeout)
            return resp.json()
    except ValueError:
        logger.warning("Telegram %s: non-JSON response", method)
    except requests.exceptions.Timeout:
        logger.error("Telegram %s timed out", method)
    except requests.exceptions.RequestException as e:
        logger.error("Telegram %s error: %s", method, e)
    return None


def send(chat_id, text, parse_mode=None):
    """Send one message to a chat id. Returns (ok: bool, message_id | error_string).

    Retries up to MAX_SEND_ATTEMPTS (3) on TRANSIENT failures — network/timeout, rate-limit (429),
    or server-side 5xx — with a short backoff (honouring Telegram's retry_after, capped). Permanent
    errors (bad chat id, bot blocked/removed, etc.) fail immediately without wasting retries.
    If parse_mode is rejected (400), we drop it and resend as plain text (doesn't burn a retry)."""
    tok = _token()
    if not tok:
        return False, "Telegram not configured (set the bot token on the Telegram page)"
    chat_id = str(chat_id).strip()
    if not chat_id:
        return False, "chat_id is required"
    text = (text or "")[:MAX_TEXT]
    if not text.strip():
        return False, "text is required"

    payload = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode

    last_err = "Telegram unreachable"
    for attempt in range(MAX_SEND_ATTEMPTS):
        data = _call("sendMessage", payload, token=tok)
        # Formatting rejected → drop parse_mode and resend as plain text (same attempt).
        if (data and data.get("ok") is False and payload.get("parse_mode")
                and data.get("error_code") == 400):
            payload.pop("parse_mode", None)
            data = _call("sendMessage", payload, token=tok)

        if data and data.get("ok"):
            return True, str(data.get("result", {}).get("message_id", ""))

        # Decide whether this failure is worth another try.
        if data is None:
            last_err, retryable, retry_after = "Telegram unreachable", True, None
        else:
            last_err = (data.get("description") or "Telegram error")[:300]
            retryable = data.get("error_code") in _RETRYABLE_CODES
            retry_after = (data.get("parameters") or {}).get("retry_after")

        if attempt < MAX_SEND_ATTEMPTS - 1 and retryable:
            time.sleep(min(retry_after or _BACKOFF[attempt], _MAX_SLEEP))
            continue
        break
    return False, last_err


def get_me(token=None):
    """Verify a token (used by the Telegram page's 'Verify bot' button). Returns the bot dict or None."""
    data = _call("getMe", None, http="get", token=token)
    if data and data.get("ok"):
        return data["result"]
    return None


def discover_chats(token=None):
    """Return the distinct chats our bot has recently seen (getUpdates), so the admin/partner can
    copy a chat id after adding the bot to a group or starting it. Only reflects the last ~24h and
    requires no webhook. List of {chat_id, title, type}."""
    data = _call("getUpdates", None, http="get", token=token, timeout=6.0)
    if not data or not data.get("ok"):
        return []
    seen = {}
    for upd in data.get("result", []):
        # A message, my_chat_member (bot added/removed), etc. all carry a chat.
        obj = (
            upd.get("message") or upd.get("my_chat_member") or upd.get("channel_post")
            or upd.get("chat_member") or {}
        )
        chat = obj.get("chat") or {}
        cid = chat.get("id")
        if cid is None or cid in seen:
            continue
        title = chat.get("title") or " ".join(
            p for p in [chat.get("first_name"), chat.get("last_name")] if p
        ) or chat.get("username") or ""
        seen[cid] = {"chat_id": str(cid), "title": title, "type": chat.get("type", "")}
    return list(seen.values())
