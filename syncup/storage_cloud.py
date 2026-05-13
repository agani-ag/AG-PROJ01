"""Cloudinary cloud storage helper.

Two modes:
  1. Direct — Cloudinary SDK (local dev / servers that can reach Cloudinary)
  2. Proxy  — Sends request details to a universal HTTP proxy on VPS

Set CLOUD_PROXY_URL in settings to enable proxy mode.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import time
from typing import Optional, Tuple

import requests as http
from django.conf import settings

logger = logging.getLogger(__name__)

_configured = False


# ── Proxy helper ─────────────────────────────────────────────────────

def _use_proxy() -> bool:
    return bool(getattr(settings, "CLOUD_PROXY_URL", ""))


def _proxy_forward(method: str, url: str, *, params=None, data=None,
                   json_body=None, auth=None, file_b64=None,
                   file_field="file", file_name="upload",
                   timeout=120) -> http.Response:
    """Send an HTTP request through the universal proxy. Returns raw Response."""
    proxy_url = getattr(settings, "CLOUD_PROXY_URL", "").rstrip("/") + "/proxy"

    payload = {"method": method, "url": url, "timeout": timeout}
    if params:
        payload["params"] = params
    if data:
        payload["data"] = data
    if json_body:
        payload["json_body"] = json_body
    if auth:
        payload["auth"] = list(auth)
    if file_b64:
        payload["file_b64"] = file_b64
        payload["file_field"] = file_field
        payload["file_name"] = file_name

    resp = http.post(
        proxy_url,
        json=payload,
        timeout=timeout + 10,
    )
    resp.raise_for_status()
    return resp


def _ensure_configured():
    global _configured
    if _configured:
        return
    if not _use_proxy():
        import cloudinary
        cloudinary.config(
            cloud_name=settings.CLOUDINARY_CLOUD_NAME,
            api_key=settings.CLOUDINARY_API_KEY,
            api_secret=settings.CLOUDINARY_API_SECRET,
            secure=True,
        )
    _configured = True


def _cloud_base() -> str:
    return f"https://api.cloudinary.com/v1_1/{settings.CLOUDINARY_CLOUD_NAME}"


def _cloud_sign(params: dict) -> str:
    """Cloudinary SHA-1 signature: sorted params + api_secret."""
    s = "&".join(f"{k}={v}" for k, v in sorted(params.items()) if v is not None)
    return hashlib.sha1((s + settings.CLOUDINARY_API_SECRET).encode()).hexdigest()


def _cloud_auth() -> tuple:
    """Basic auth tuple for Cloudinary Admin API."""
    return (settings.CLOUDINARY_API_KEY, settings.CLOUDINARY_API_SECRET)


def is_enabled() -> bool:
    return bool(getattr(settings, "USE_CLOUD_STORAGE", False))


def _resource_type(content_type: Optional[str]) -> str:
    """Map MIME type to Cloudinary resource_type: image, video, or raw."""
    if not content_type:
        return "raw"
    ct = content_type.lower()
    if ct.startswith("image/"):
        return "image"
    if ct.startswith("video/") or ct.startswith("audio/"):
        return "video"
    return "raw"


def _folder() -> str:
    return getattr(settings, "CLOUDINARY_FOLDER", "syncup") or "syncup"


def upload_file(local_path: str, key: str, content_type: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """Upload a local file. Returns (public_url, public_id)."""
    with open(local_path, "rb") as f:
        data = f.read()
    return upload_bytes(data, key, content_type)


def upload_bytes(data: bytes, key: str, content_type: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """Upload raw bytes. Returns (public_url, public_id)."""
    _ensure_configured()

    rtype = _resource_type(content_type)
    folder = _folder()
    parts = key.split("/", 1)
    if len(parts) == 2:
        folder = f"{folder}/{parts[0]}"
        public_id = os.path.splitext(parts[1])[0]
    else:
        public_id = os.path.splitext(key)[0]

    if _use_proxy():
        ts = str(int(time.time()))
        sig_params = {
            "folder": folder,
            "overwrite": "true",
            "public_id": public_id,
            "timestamp": ts,
        }
        form_data = {
            **sig_params,
            "api_key": settings.CLOUDINARY_API_KEY,
            "signature": _cloud_sign(sig_params),
        }
        resp = _proxy_forward(
            "POST",
            f"{_cloud_base()}/{rtype}/upload",
            data=form_data,
            file_b64=base64.b64encode(data).decode(),
            timeout=120,
        )
        result = resp.json()
        return result.get("secure_url", ""), result.get("public_id", "")
    else:
        import cloudinary.uploader
        from io import BytesIO

        result = cloudinary.uploader.upload(
            BytesIO(data),
            folder=folder,
            public_id=public_id,
            resource_type=rtype,
            overwrite=True,
        )
        return result.get("secure_url", ""), result.get("public_id", "")


def download_to_path(key: str, local_path: str) -> None:
    """Download a Cloudinary resource to a local file path."""
    _ensure_configured()

    if _use_proxy():
        resp = _proxy_forward("GET", key, timeout=120)
        with open(local_path, "wb") as f:
            f.write(resp.content)
    else:
        resp = http.get(key, timeout=120)
        resp.raise_for_status()
        with open(local_path, "wb") as f:
            f.write(resp.content)


def delete_key(public_id: str, content_type: Optional[str] = None) -> bool:
    """Delete a resource by public_id. Returns True on success."""
    if not public_id:
        return False
    _ensure_configured()

    if _use_proxy():
        try:
            ts = str(int(time.time()))
            sig_params = {"public_id": public_id, "timestamp": ts}
            form_data = {
                **sig_params,
                "api_key": settings.CLOUDINARY_API_KEY,
                "signature": _cloud_sign(sig_params),
            }
            for rtype in ("image", "video", "raw"):
                resp = _proxy_forward(
                    "POST",
                    f"{_cloud_base()}/{rtype}/destroy",
                    data=form_data,
                    timeout=30,
                )
                if resp.json().get("result") == "ok":
                    return True
            return False
        except Exception:
            logger.exception("Proxy delete failed for %s", public_id)
            return False
    else:
        import cloudinary.uploader

        for rtype in ("image", "video", "raw"):
            try:
                result = cloudinary.uploader.destroy(public_id, resource_type=rtype)
                if result.get("result") == "ok":
                    return True
            except Exception:
                continue
        logger.warning("Failed to delete Cloudinary resource: %s", public_id)
        return False


def list_resources(resource_type: str, prefix: str, next_cursor=None, max_results=48):
    """List Cloudinary resources. Returns (resources_list, next_cursor)."""
    _ensure_configured()

    if _use_proxy():
        try:
            params = {
                "type": "upload",
                "prefix": prefix,
                "max_results": max_results,
                "direction": -1,
            }
            if next_cursor:
                params["next_cursor"] = next_cursor

            resp = _proxy_forward(
                "GET",
                f"{_cloud_base()}/resources/{resource_type}",
                params=params,
                auth=_cloud_auth(),
                timeout=30,
            )
            result = resp.json()
            return result.get("resources", []), result.get("next_cursor")
        except Exception:
            logger.exception("Proxy list failed for %s/%s", resource_type, prefix)
            return [], None
    else:
        import cloudinary.api

        params = {
            "type": "upload",
            "prefix": prefix,
            "max_results": max_results,
            "direction": -1,
        }
        if next_cursor:
            params["next_cursor"] = next_cursor

        try:
            result = cloudinary.api.resources(resource_type=resource_type, **params)
            return result.get("resources", []), result.get("next_cursor")
        except Exception:
            logger.exception("Cloudinary list failed for %s/%s", resource_type, prefix)
            return [], None
