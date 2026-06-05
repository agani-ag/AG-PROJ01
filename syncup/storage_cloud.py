"""Cloudinary cloud storage helper (direct mode only)."""
from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Optional, Tuple

import requests as http
from django.conf import settings

logger = logging.getLogger(__name__)

_configured = False


def _ensure_configured():
    global _configured
    if _configured:
        return
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

    resp = http.get(key, timeout=120)
    resp.raise_for_status()
    with open(local_path, "wb") as f:
        f.write(resp.content)


def delete_key(public_id: str, content_type: Optional[str] = None) -> bool:
    """Delete a resource by public_id. Returns True on success."""
    if not public_id:
        return False
    _ensure_configured()

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


def get_resource_info(public_id: str, resource_type: str = "image") -> dict:
    """Fetch full metadata for a Cloudinary resource."""
    _ensure_configured()

    import cloudinary.api

    try:
        return cloudinary.api.resource(
            public_id,
            resource_type=resource_type,
            image_metadata=True,
            colors=True,
            faces=True,
            quality_analysis=True,
            accessibility_analysis=True,
            phash=True,
            pages=True,
            media_metadata=True,
            tags=True,
            context=True,
            metadata=True,
        )
    except Exception:
        logger.exception("Cloudinary resource info failed for %s (%s)", public_id, resource_type)
        return {}


def check_duplicate(public_id: str, threshold: float = 0.8) -> dict:
    """Run Cloudinary Duplicate Detection on an existing asset via explicit API.

    Returns the full response dict including moderation results.
    """
    _ensure_configured()

    ts = str(int(time.time()))
    moderation_val = f"duplicate:{threshold}"
    sig_params = {
        "moderation": moderation_val,
        "public_id": public_id,
        "timestamp": ts,
        "type": "upload",
    }

    form_data = {
        **sig_params,
        "api_key": settings.CLOUDINARY_API_KEY,
        "signature": _cloud_sign(sig_params),
    }

    try:
        resp = http.post(
            f"{_cloud_base()}/image/explicit",
            data=form_data,
            timeout=30,
        )
        return resp.json()
    except Exception:
        logger.exception("Explicit/duplicate check failed for %s", public_id)
        return {"error": True, "public_id": public_id}
