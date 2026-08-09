"""
Firebase Remote Config REST helper (server-side, uses the service account).

Lets the admin read/update Remote Config parameters (e.g. `api_base_url`) from
the Django admin, without opening the Firebase console. Reuses the same
service account / project as FCM (`FIREBASE_PROJECT_ID`, `SERVICE_ACCOUNT_FILE`).
"""
import json

import requests
from django.conf import settings

from .fcm import _service_account_path, is_configured  # reuse project credentials

RC_SCOPES = ["https://www.googleapis.com/auth/firebase.remoteconfig"]
_URL = "https://firebaseremoteconfig.googleapis.com/v1/projects/{pid}/remoteConfig"


class RemoteConfigError(Exception):
    pass


def _project_id():
    pid = getattr(settings, "FIREBASE_PROJECT_ID", None)
    if not pid:
        raise RemoteConfigError("FIREBASE_PROJECT_ID is not set")
    return pid


def _access_token():
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_file(
        _service_account_path(), scopes=RC_SCOPES
    )
    creds.refresh(Request())
    return creds.token


def get_template():
    """Returns (template_dict, etag)."""
    url = _URL.format(pid=_project_id())
    resp = requests.get(url, headers={"Authorization": f"Bearer {_access_token()}"}, timeout=15)
    if resp.status_code != 200:
        raise RemoteConfigError(f"{resp.status_code}: {resp.text[:300]}")
    etag = resp.headers.get("ETag", "*")
    return resp.json(), etag


def get_parameter(key):
    """Returns the parameter's default value string (or None if unset)."""
    template, _etag = get_template()
    param = (template.get("parameters") or {}).get(key)
    if not param:
        return None
    return (param.get("defaultValue") or {}).get("value")


def set_parameter(key, value):
    """Set/update a parameter's default value, preserving other params + fields."""
    template, etag = get_template()
    params = template.setdefault("parameters", {})
    existing = params.get(key) or {}
    existing["defaultValue"] = {"value": value}
    params[key] = existing

    url = _URL.format(pid=_project_id())
    headers = {
        "Authorization": f"Bearer {_access_token()}",
        "Content-Type": "application/json; UTF-8",
        "If-Match": etag or "*",
    }
    resp = requests.put(url, headers=headers, data=json.dumps(template), timeout=20)
    if resp.status_code != 200:
        raise RemoteConfigError(f"{resp.status_code}: {resp.text[:300]}")
    return True
