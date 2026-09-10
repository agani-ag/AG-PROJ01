"""
Self-contained Firebase Cloud Messaging (v1) sender for the SyncUp app.

The SyncUp Firebase project (`syncup-f470b`) hosts both the existing app and the
new `com.agani.syncup` app, and a service account is per-project — so this reuses
the project's existing credentials (own code, no import of the other app's helper):

    FIREBASE_PROJECT_ID     e.g. "syncup-f470b"
    SERVICE_ACCOUNT_FILE    path to the service-account JSON (abs, or relative to BASE_DIR)
"""
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

import requests
from requests.adapters import HTTPAdapter
from django.conf import settings

FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
_SEND_URL = "https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"

# Max concurrent FCM HTTP calls per send(). A broadcast fans out across these instead of
# going one token at a time, so the cron endpoint responds fast and stays inside its budget.
MAX_SEND_WORKERS = 16

# OAuth tokens are valid ~1h; cache and reuse across sends in a run, refreshing a little early.
_token_cache = {"token": None, "expires_at": 0.0}


class FCMError(Exception):
    pass


# Friendly option keys → FCM v1 AndroidConfig. These only affect SYSTEM-rendered notifications
# (app backgrounded); when the app is foregrounded its own builder draws the notification. On
# Android 8+ sound/importance are governed by the app's notification channel, so those act as
# hints. `raw` is a dict deep-merged last as an escape hatch for any AndroidConfig field.
def _deep_merge(base, extra):
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def _vibrate_timings(text):
    """"0.25,0.25,0.5" → ["0.25s","0.25s","0.5s"] (FCM duration strings); [] if unparseable."""
    out = []
    for part in str(text).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(f"{float(part)}s")
        except ValueError:
            return []
    return out


def _light_settings(hex_color, on_ms, off_ms):
    """LED color (hex) + on/off durations → FCM light_settings block."""
    h = str(hex_color).lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    on = (int(on_ms) if on_ms else 1000) / 1000.0
    off = (int(off_ms) if off_ms else 1000) / 1000.0
    return {
        "color": {"red": r, "green": g, "blue": b, "alpha": 1.0},
        "light_on_duration": f"{on}s",
        "light_off_duration": f"{off}s",
    }


def build_android_config(opts):
    """Turn the Push form's option dict into an FCM `android` block, or return None if empty.

    Recognised keys (all optional):
      android-level:  priority ("high"/"normal"), ttl_seconds (int), collapse_tag (str),
                      restrict_package (bool → restricted_package_name), analytics_label (str)
      notification:   sound ("default"/"silent"), color (hex), notification_priority
                      ("min|low|default|high|max"), visibility ("private|public|secret"),
                      sticky (bool), local_only (bool), notification_count (int), ticker (str),
                      event_time (RFC3339 str), vibrate (str), led_color (hex) + led_on_ms/led_off_ms
      raw (dict):     deep-merged last — escape hatch for any other AndroidConfig field.
    """
    opts = {k: v for k, v in (opts or {}).items() if v not in (None, "", [])}
    if not opts:
        return None

    android = {}
    notif = {}

    # ---- android-level ----
    if opts.get("priority"):
        android["priority"] = str(opts["priority"]).upper()  # HIGH | NORMAL
    if opts.get("ttl_seconds") not in (None, ""):
        android["ttl"] = f"{int(opts['ttl_seconds'])}s"
    if opts.get("collapse_tag"):
        android["collapse_key"] = str(opts["collapse_tag"])
        notif["tag"] = str(opts["collapse_tag"])  # also replace the shown notification
    if opts.get("restrict_package"):
        android["restricted_package_name"] = "com.agani.syncup"
    if opts.get("analytics_label"):
        android["fcm_options"] = {"analytics_label": str(opts["analytics_label"])}

    # ---- notification-level ----
    sound = opts.get("sound")
    if sound == "default":
        notif["sound"] = "default"
        notif["default_sound"] = True
    elif sound == "silent":
        notif["default_sound"] = False

    if opts.get("color"):
        notif["color"] = str(opts["color"])
    if opts.get("notification_priority"):
        notif["notification_priority"] = "PRIORITY_" + str(opts["notification_priority"]).upper()
    if opts.get("visibility"):
        notif["visibility"] = str(opts["visibility"]).upper()  # PRIVATE | PUBLIC | SECRET
    if opts.get("channel_id"):
        # Routes the SYSTEM-rendered notification (app backgrounded/killed) to this channel, which
        # must exist in the app. Use a high-importance channel for a heads-up banner.
        notif["channel_id"] = str(opts["channel_id"])
    if opts.get("sticky"):
        notif["sticky"] = True
    if opts.get("local_only"):
        notif["local_only"] = True
    if opts.get("notification_count") not in (None, ""):
        notif["notification_count"] = int(opts["notification_count"])
    if opts.get("ticker"):
        notif["ticker"] = str(opts["ticker"])
    if opts.get("event_time"):
        notif["event_time"] = str(opts["event_time"])  # RFC3339, e.g. 2026-01-01T12:00:00Z
    if opts.get("vibrate"):
        timings = _vibrate_timings(opts["vibrate"])
        if timings:
            notif["default_vibrate_timings"] = False
            notif["vibrate_timings"] = timings
    if opts.get("led_color"):
        notif["light_settings"] = _light_settings(
            opts["led_color"], opts.get("led_on_ms"), opts.get("led_off_ms"),
        )

    if notif:
        android["notification"] = notif
    if isinstance(opts.get("raw"), dict):
        _deep_merge(android, opts["raw"])
    return android or None


def is_configured():
    return bool(
        getattr(settings, "FIREBASE_PROJECT_ID", None)
        and getattr(settings, "SERVICE_ACCOUNT_FILE", None)
    )


def _service_account_path():
    sa_file = getattr(settings, "SERVICE_ACCOUNT_FILE", None)
    if not sa_file:
        raise FCMError("SERVICE_ACCOUNT_FILE is not set")
    if not os.path.isabs(sa_file):
        sa_file = os.path.join(settings.BASE_DIR, sa_file)
    if not os.path.exists(sa_file):
        raise FCMError(f"Service-account file not found: {sa_file}")
    return sa_file


def _access_token():
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]

    # Imported lazily so the app loads even if google-auth isn't installed yet.
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_file(
        _service_account_path(), scopes=[FCM_SCOPE]
    )
    creds.refresh(Request())
    # Refresh 10 min before the ~1h expiry so a long run never sends with a just-expired token.
    _token_cache["token"] = creds.token
    _token_cache["expires_at"] = now + 50 * 60
    return creds.token


def send(tokens, title, body, data=None, image=None, timeout=15, max_workers=MAX_SEND_WORKERS,
         android=None):
    """Send a notification to each FCM token. Returns (success_count, fail_count).

    `image` (an HTTPS URL) is attached to the notification so Android shows a big-picture
    notification — automatically when the app is backgrounded, and via the app when foregrounded.

    `android` is an optional FCM AndroidConfig block (build it with build_android_config) carrying
    priority/sound/color/collapse/ttl etc.; it only affects system-rendered (backgrounded) pushes.

    Sends fan out across a thread pool (`max_workers`) over one keep-alive HTTPS connection,
    so a broadcast to many devices completes in roughly len(tokens)/max_workers rounds instead
    of one token at a time. `timeout` is per token.
    """
    tokens = [t for t in (tokens or []) if t]
    if not tokens:
        return 0, 0

    project_id = getattr(settings, "FIREBASE_PROJECT_ID", None)
    if not project_id:
        raise FCMError("FIREBASE_PROJECT_ID is not set")

    access_token = _access_token()
    url = _SEND_URL.format(project_id=project_id)
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

    base_notification = {"title": title, "body": body}
    if image:
        base_notification["image"] = image
    str_data = {str(k): str(v) for k, v in data.items()} if data else None

    workers = max(1, min(max_workers, len(tokens)))
    # One pooled Session so the parallel workers reuse keep-alive connections to FCM instead of
    # each doing a fresh TCP+TLS handshake. urllib3's pool is thread-safe.
    session = requests.Session()
    session.mount("https://", HTTPAdapter(pool_connections=workers, pool_maxsize=workers))

    def _post(token):
        """Send to one token. Returns (ok: bool, dead_token or None)."""
        message = {"message": {"token": token, "notification": base_notification}}
        if str_data:
            message["message"]["data"] = str_data
        if android:
            message["message"]["android"] = android
        try:
            resp = session.post(url, headers=headers, data=json.dumps(message), timeout=timeout)
        except requests.RequestException:
            return False, None
        if resp.status_code == 200:
            return True, None
        # A stale/unregistered token → stop sending to it in future.
        dead = resp.status_code == 404 or "UNREGISTERED" in resp.text or "NOT_FOUND" in resp.text
        return False, (token if dead else None)

    success = 0
    fail = 0
    dead_tokens = []
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for ok, dead in pool.map(_post, tokens):
                if ok:
                    success += 1
                else:
                    fail += 1
                if dead:
                    dead_tokens.append(dead)
    finally:
        session.close()

    _deactivate_dead_tokens(dead_tokens)
    return success, fail


def _deactivate_dead_tokens(tokens):
    """Mark devices whose FCM token was rejected as unregistered as inactive (best-effort)."""
    if not tokens:
        return
    try:
        from .models import AppDevice
        AppDevice.objects.filter(fcm_token__in=tokens).update(is_active=False)
    except Exception:
        pass
