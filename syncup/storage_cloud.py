"""Cloudinary cloud storage helper.

Provides the same interface the rest of the codebase expects:
  is_enabled(), upload_file(), upload_bytes(), download_to_path(), delete_key()

Activated when CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, and
CLOUDINARY_API_SECRET are set in settings.
"""
from __future__ import annotations

import logging
import os
from typing import Optional, Tuple

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
    _ensure_configured()
    import cloudinary.uploader

    rtype = _resource_type(content_type)
    # key is like "device_id/filename.jpg" — use device_id as subfolder
    folder = _folder()
    parts = key.split("/", 1)
    if len(parts) == 2:
        folder = f"{folder}/{parts[0]}"
        public_id = os.path.splitext(parts[1])[0]
    else:
        public_id = os.path.splitext(key)[0]

    result = cloudinary.uploader.upload(
        local_path,
        folder=folder,
        public_id=public_id,
        resource_type=rtype,
        overwrite=True,
    )
    url = result.get("secure_url", "")
    full_public_id = result.get("public_id", "")
    return url, full_public_id


def upload_bytes(data: bytes, key: str, content_type: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """Upload raw bytes. Returns (public_url, public_id)."""
    _ensure_configured()
    import cloudinary.uploader
    from io import BytesIO

    rtype = _resource_type(content_type)
    folder = _folder()
    parts = key.split("/", 1)
    if len(parts) == 2:
        folder = f"{folder}/{parts[0]}"
        public_id = os.path.splitext(parts[1])[0]
    else:
        public_id = os.path.splitext(key)[0]

    result = cloudinary.uploader.upload(
        BytesIO(data),
        folder=folder,
        public_id=public_id,
        resource_type=rtype,
        overwrite=True,
    )
    url = result.get("secure_url", "")
    full_public_id = result.get("public_id", "")
    return url, full_public_id


def download_to_path(key: str, local_path: str) -> None:
    """Download a Cloudinary resource to a local file path.
    `key` here is the secure_url or public_id — we use the URL stored in final_url."""
    import requests as req
    # key should be the full URL for download
    resp = req.get(key, timeout=120)
    resp.raise_for_status()
    with open(local_path, "wb") as f:
        f.write(resp.content)


def delete_key(public_id: str, content_type: Optional[str] = None) -> bool:
    """Delete a resource by public_id. Returns True on success."""
    if not public_id:
        return False
    _ensure_configured()
    import cloudinary.uploader

    # Try all resource types since we may not know the exact type
    for rtype in ("image", "video", "raw"):
        try:
            result = cloudinary.uploader.destroy(public_id, resource_type=rtype)
            if result.get("result") == "ok":
                return True
        except Exception:
            continue
    logger.warning("Failed to delete Cloudinary resource: %s", public_id)
    return False
