"""Each person's Cloud folder, checked with Cloudinary by this server (user, 27 Nov 2026: "they will have full
access" — every file in their own folder can be renamed and deleted, however it got there).

When someone opens Cloud (the app asks for cloud/look or cloud/files), this server asks Cloudinary's Search API
what is in <CLOUDINARY_FOLDER>/accounts/<id> and its subfolders, and brings its own list up to date:

- files put there outside SyncUp (Cloudinary's website, the console) join the list — then the phone can rename
  and delete them, because this server now knows they really are in that person's folder;
- a file moved on Cloudinary follows (into another person's folder: it becomes theirs);
- a file deleted on Cloudinary leaves the list.

The answer comes from Cloudinary itself (with the API key), so nothing a phone says is trusted. PythonAnywhere's
allowlist has ".cloudinary.com"; if Cloudinary can't be reached, nothing changes and the list stays as it was.
At most once a minute per person (Cloudinary's Admin API allows a few hundred calls an hour).
"""
import logging
from datetime import timedelta

import requests
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from .cloud_api import (_SEARCH_RESERVED, RESOURCE_TYPES, account_root, cloud_name, configured, folder_mode, keys,
                        ping_cloud)
from .cloud_notify import _ext, _float, _int, _name, _under
from .models import AppAccount, CloudFile

logger = logging.getLogger(__name__)

SYNC_EVERY = 60          # seconds between checks of one person's folder
PAGES = 10               # 500 a page: up to 5,000 files are checked
NEW_FILES_KEPT = timedelta(minutes=5)  # Cloudinary's search can take a moment to have a new upload


def cloudinary_post(path, body):
    """Cloudinary's Admin API (POST, JSON) with the API key — the network call, kept apart for the tests."""
    key, secret = keys()
    r = requests.post(f"https://api.cloudinary.com/v1_1/{cloud_name()}/{path}", json=body, auth=(key, secret),
                      timeout=(5, 15))
    r.raise_for_status()
    return r.json()


def search_folder(root):
    """Every file in `root` and its subfolders, as Cloudinary has them: (files, complete). complete is False when
    there were more than PAGES pages (then nothing is taken off the list)."""
    term = "".join("\\" + c if c in _SEARCH_RESERVED else c for c in root)
    field = "folder" if folder_mode() == "fixed" else "asset_folder"
    body = {"expression": f"{field}={term} OR {field}:{term}/*", "max_results": 500}
    found = []
    for _ in range(PAGES):
        d = cloudinary_post("resources/search", body)
        found += [r for r in d.get("resources") or [] if isinstance(r, dict)]
        cursor = d.get("next_cursor")
        if not cursor:
            return found, True
        body = {**body, "next_cursor": cursor}
    return found, False


def _folder_of(r):
    if folder_mode() == "fixed":
        pid = str(r.get("public_id") or "")
        return pid.rsplit("/", 1)[0] if "/" in pid else ""
    return str(r.get("asset_folder") or "").strip("/")


def apply(account, found, complete, now=None):
    """Bring this person's part of the list in line with what Cloudinary has. Returns the accounts whose list
    changed (theirs, and anyone a file was moved away from)."""
    root = account_root(account.id)
    now = now or timezone.now()
    owners = set()
    seen = set()
    for r in found:
        rtype, pid = r.get("resource_type"), str(r.get("public_id") or "")
        folder = _folder_of(r)
        if rtype not in RESOURCE_TYPES or not pid or (r.get("type") or "upload") != "upload" or not _under(folder, root):
            continue
        seen.add((rtype, pid))
        fmt = str(r.get("format") or "")[:20]
        fields = {"owner_id": account.id, "folder": folder, "format": fmt, "bytes": _int(r.get("bytes")) or 0,
                  "version": str(r.get("version") or "")[:20]}
        for k in ("width", "height"):
            if _int(r.get(k)):
                fields[k] = _int(r.get(k))
        if _float(r.get("duration")):
            fields["duration"] = _float(r.get("duration"))
        if r.get("secure_url"):
            fields["url"] = str(r["secure_url"])[:1000]
        f = CloudFile.objects.filter(resource_type=rtype, public_id=pid).first()
        if f is None:
            if "url" not in fields:
                continue
            CloudFile.objects.create(resource_type=rtype, public_id=pid, account=account,
                                     name=_name(r.get("display_name"), pid, _ext(rtype, pid, fmt)), **fields)
            owners.add(account.id)
            continue
        if f.account_id == account.id and all(getattr(f, k) == v for k, v in fields.items()):
            continue
        owners.update({f.owner_id, account.id})  # moved here, or changed on Cloudinary: keep the name it has
        for k, v in fields.items():
            setattr(f, k, v)
        f.account = account
        f.save()
    if complete:
        for f in CloudFile.objects.filter(owner_id=account.id, created_at__lt=now - NEW_FILES_KEPT):
            if (f.resource_type, f.public_id) not in seen:
                f.delete()  # deleted on Cloudinary, or moved out of this person's folder
                owners.add(account.id)
    return owners


def sync_own_folder(account_id):
    """Check one person's folder with Cloudinary, at most once a minute. Returns "checked", "recent" (checked
    within the minute), "unreachable" or "off"."""
    if not configured():
        return "off"
    if not cache.add(f"cloud_sync:{account_id}", 1, SYNC_EVERY):
        return "recent"
    account = AppAccount.objects.filter(pk=account_id).first()
    if account is None:
        return "off"
    try:
        found, complete = search_folder(account_root(account_id))
        with transaction.atomic():
            owners = apply(account, found, complete)
    except Exception as e:  # noqa: BLE001 — never fail opening Cloud over this check
        logger.warning("Couldn't check Cloud folder %s with Cloudinary: %s", account_id, e)
        return "unreachable"
    if owners:
        ping_cloud(owners)  # their open Cloud pages reload
    return "checked"
