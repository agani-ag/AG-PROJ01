"""Filebase (S3-compatible IPFS) storage helper.

Activated when FILEBASE_BUCKET, FILEBASE_ACCESS_KEY, and FILEBASE_SECRET_KEY
are configured in settings. Provides a tiny wrapper around boto3 so the rest
of the codebase doesn't need to know about S3 details.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional, Tuple

from django.conf import settings

logger = logging.getLogger(__name__)

_client_lock = threading.Lock()
_client = None


def is_enabled() -> bool:
    return bool(getattr(settings, "USE_FILEBASE_STORAGE", False))


def get_client():
    """Return a cached boto3 S3 client pointing at Filebase."""
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is not None:
            return _client
        import boto3
        _client = boto3.client(
            "s3",
            endpoint_url=settings.FILEBASE_ENDPOINT,
            aws_access_key_id=settings.FILEBASE_ACCESS_KEY,
            aws_secret_access_key=settings.FILEBASE_SECRET_KEY,
            region_name=settings.FILEBASE_REGION,
        )
    return _client


def _build_url(key: str, cid: Optional[str]) -> str:
    mode = getattr(settings, "FILEBASE_URL_MODE", "ipfs")
    if mode == "ipfs" and cid:
        return ipfs_url(cid)
    return f"https://{settings.FILEBASE_BUCKET}.s3.filebase.com/{key}"


def ipfs_url(cid: str) -> str:
    """Build a public IPFS gateway URL using the configured gateway host."""
    host = getattr(settings, "FILEBASE_IPFS_GATEWAY", "ipfs.filebase.io") or "ipfs.filebase.io"
    return f"https://{host}/ipfs/{cid}"


def _get_cid(s3, key: str, retries: int = 6, delay: float = 0.5) -> Optional[str]:
    """Filebase pins to IPFS asynchronously — poll head_object until 'cid' metadata appears."""
    import time
    for attempt in range(retries):
        try:
            head = s3.head_object(Bucket=settings.FILEBASE_BUCKET, Key=key)
            cid = head.get("Metadata", {}).get("cid")
            if cid:
                return cid
        except Exception:
            logger.exception("head_object failed for key %s (attempt %s)", key, attempt + 1)
            return None
        time.sleep(delay)
        delay = min(delay * 1.5, 3.0)
    logger.warning("CID not available after %s retries for key %s", retries, key)
    return None


def upload_file(local_path: str, key: str, content_type: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """Upload a file. Returns (public_url, ipfs_cid_or_none)."""
    s3 = get_client()
    extra = {"ACL": "public-read"}
    if content_type:
        extra["ContentType"] = content_type
    with open(local_path, "rb") as f:
        s3.upload_fileobj(f, settings.FILEBASE_BUCKET, key, ExtraArgs=extra)
    cid = _get_cid(s3, key)
    return _build_url(key, cid), cid


def upload_bytes(data: bytes, key: str, content_type: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """Upload raw bytes. Returns (public_url, ipfs_cid_or_none)."""
    from io import BytesIO
    s3 = get_client()
    extra = {"ACL": "public-read"}
    if content_type:
        extra["ContentType"] = content_type
    s3.upload_fileobj(BytesIO(data), settings.FILEBASE_BUCKET, key, ExtraArgs=extra)
    cid = _get_cid(s3, key)
    return _build_url(key, cid), cid


def download_to_path(key: str, local_path: str) -> None:
    """Download an object to a local file path."""
    s3 = get_client()
    with open(local_path, "wb") as f:
        s3.download_fileobj(settings.FILEBASE_BUCKET, key, f)


def delete_key(key: str) -> bool:
    """Delete an object. Returns True on success (or if it didn't exist)."""
    if not key:
        return False
    try:
        s3 = get_client()
        s3.delete_object(Bucket=settings.FILEBASE_BUCKET, Key=key)
        return True
    except Exception:
        logger.exception("Failed to delete S3 key %s", key)
        return False
