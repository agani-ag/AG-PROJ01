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

import requests
from django.conf import settings

FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
_SEND_URL = "https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"


class FCMError(Exception):
    pass


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
    # Imported lazily so the app loads even if google-auth isn't installed yet.
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_file(
        _service_account_path(), scopes=[FCM_SCOPE]
    )
    creds.refresh(Request())
    return creds.token


def send(tokens, title, body, data=None):
    """Send a notification to each FCM token. Returns (success_count, fail_count)."""
    tokens = [t for t in (tokens or []) if t]
    if not tokens:
        return 0, 0

    project_id = getattr(settings, "FIREBASE_PROJECT_ID", None)
    if not project_id:
        raise FCMError("FIREBASE_PROJECT_ID is not set")

    access_token = _access_token()
    url = _SEND_URL.format(project_id=project_id)
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

    success = 0
    fail = 0
    for token in tokens:
        message = {"message": {"token": token, "notification": {"title": title, "body": body}}}
        if data:
            message["message"]["data"] = {str(k): str(v) for k, v in data.items()}
        try:
            resp = requests.post(url, headers=headers, data=json.dumps(message), timeout=15)
            if resp.status_code == 200:
                success += 1
            else:
                fail += 1
        except requests.RequestException:
            fail += 1
    return success, fail
