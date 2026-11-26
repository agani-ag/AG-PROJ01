"""Cloudinary's webhook notifications → this server's list of each person's Cloud folder.

The rule (user, 26 Nov 2026): everything inside <CLOUDINARY_FOLDER>/accounts/<id>/… belongs to that account and
shows in their app — however it got there: SyncUp's app, the admin console, or Cloudinary's own website (upload,
move, rename, delete). This server can't ask Cloudinary (PythonAnywhere blocks those calls), but Cloudinary can
call us. Set up once in Cloudinary: Console → Settings → Webhook Notifications → add
https://<this server>/app/v1/cloud/notify, notification types "All", signed with the same API key as the
server's CLOUDINARY_API_KEY. Every notification is checked against the API secret (and its age) before use.

Nothing on the phones changes: their folder list comes from this server, and a silent push makes an open Cloud
page reload. Dynamic folder mode: a file's folder is its asset_folder, not part of its public id.
"""
import hashlib
import hmac
import json
import logging
import time

from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .cloud_api import RESOURCE_TYPES, _stem_ext, base_folder, cloud_name, keys, ping_cloud
from .models import AppAccount, CloudFile, CloudFolder

logger = logging.getLogger(__name__)

MAX_AGE = 2 * 60 * 60          # Cloudinary's advice: refuse notifications older than 2 hours
MAX_BODY = 2 * 1024 * 1024


# --------------------------------------------------------------------------- checks
def signature_ok(body, timestamp, signature, secret, now=None):
    """Cloudinary signs a notification as hex(SHA-1 or SHA-256 of the raw body + X-Cld-Timestamp + API secret)."""
    if body is None or not (timestamp and signature and secret):
        return False
    try:
        ts = int(str(timestamp).strip())
    except ValueError:
        return False
    if abs((now if now is not None else time.time()) - ts) > MAX_AGE:
        return False
    payload = body + str(ts).encode("utf-8") + secret.encode("utf-8")
    sig = str(signature).strip()
    return any(hmac.compare_digest(algo(payload).hexdigest(), sig) for algo in (hashlib.sha1, hashlib.sha256))


def owner_of_folder(folder):
    """The account whose own folder this asset folder is, or is inside; None for any other folder."""
    prefix = base_folder() + "/accounts/"
    f = str(folder or "").strip("/")
    if not f.startswith(prefix):
        return None
    head = f[len(prefix):].split("/", 1)[0]
    return int(head) if head.isdigit() else None


def _account_for(folder):
    """(owner id, account) for a folder inside an existing account's own folder, else (None, None)."""
    owner = owner_of_folder(folder)
    account = AppAccount.objects.filter(pk=owner).first() if owner else None
    return (owner, account) if account else (None, None)


def _int(v):
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _float(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _ext(rtype, public_id, fmt):
    return str(fmt or "") or (_stem_ext(public_id)[1] if rtype == "raw" else "")


def _name(display_name, public_id, ext):
    """The name people see: Cloudinary's display name with the file's extension (as SyncUp names files)."""
    n = (str(display_name or "").strip() or str(public_id).rsplit("/", 1)[-1])[:240]
    if ext and not n.lower().endswith("." + ext.lower()):
        n = f"{n}.{ext}"
    return n[:255]


def _url(rtype, dtype, public_id, fmt, version=None):
    """A delivery address when the notification gave none (a move or rename): without an extension Cloudinary
    sends the file in its own format."""
    v = f"v{version}/" if version else ""
    ext = f".{fmt}" if fmt and rtype != "raw" else ""
    return f"https://res.cloudinary.com/{cloud_name()}/{rtype}/{dtype or 'upload'}/{v}{public_id}{ext}"


def _items(d, key="resources"):
    """(public id, entry) pairs: "resources" is a list in some notifications, keyed by public id in others; a
    notification about one file may carry it at the top level."""
    res = d.get(key)
    if isinstance(res, dict):
        return [(pid, r) for pid, r in res.items() if isinstance(r, dict)]
    if isinstance(res, list):
        return [(r.get("public_id"), r) for r in res if isinstance(r, dict)]
    return [(d.get("public_id"), d)] if d.get("public_id") else []


def _partial(rtype, pid, folder, owner, account, display_name="", dtype="upload"):
    """List a file that arrived by a move / rename / new name: Cloudinary didn't say its size or type, so they stay
    empty until the file changes again."""
    f, created = CloudFile.objects.get_or_create(
        resource_type=rtype, public_id=pid,
        defaults={"account": account, "owner_id": owner, "folder": folder, "name": _name(display_name, pid, _ext(rtype, pid, "")),
                  "url": _url(rtype, dtype, pid, "")[:1000]},
    )
    return f, created


# --------------------------------------------------------------------------- one handler per kind
def _on_upload(d):
    rtype, pid = d.get("resource_type"), str(d.get("public_id") or "")
    if rtype not in RESOURCE_TYPES or not pid or (d.get("type") or "upload") != "upload":
        return set()
    folder = str(d.get("asset_folder") or "").strip("/")
    owner, account = _account_for(folder)
    f = CloudFile.objects.filter(resource_type=rtype, public_id=pid).first()
    if account is None:
        # Not in anyone's own folder (or uploaded over a listed file into another folder): off the list.
        if f:
            f.delete()
            return {f.owner_id}
        return set()
    fmt = str(d.get("format") or "")[:20]
    fields = {
        "owner_id": owner, "folder": folder, "format": fmt, "bytes": _int(d.get("bytes")) or 0,
        "width": _int(d.get("width")), "height": _int(d.get("height")), "duration": _float(d.get("duration")),
        "version": str(d.get("version") or "")[:20],
        "url": str(d.get("secure_url") or d.get("url") or _url(rtype, "upload", pid, fmt, d.get("version")))[:1000],
    }
    if f is None:
        CloudFile.objects.create(resource_type=rtype, public_id=pid, account=account,
                                 name=_name(d.get("display_name") or d.get("original_filename"), pid, _ext(rtype, pid, fmt)),
                                 **fields)
        return {owner}
    # Already listed (SyncUp's app or console reported it): keep its name, refresh what Cloudinary knows better.
    if all(getattr(f, k) == v for k, v in fields.items()):
        return set()
    before = f.owner_id
    for k, v in fields.items():
        setattr(f, k, v)
    f.account = account
    f.save()
    return {before, owner}


def _on_delete(d):
    owners = set()
    for pid, r in _items(d):
        qs = CloudFile.objects.filter(resource_type=r.get("resource_type"), public_id=str(pid or ""))
        owners.update(qs.values_list("owner_id", flat=True))
        qs.delete()
    return owners


def _on_rename(d):
    rtype = d.get("resource_type")
    src, dst = str(d.get("from_public_id") or ""), str(d.get("to_public_id") or "")
    if rtype not in RESOURCE_TYPES or not src or not dst:
        return set()
    f = CloudFile.objects.filter(resource_type=rtype, public_id=src).first()
    if f is None:
        if CloudFile.objects.filter(resource_type=rtype, public_id=dst).exists():
            return set()  # SyncUp's app renamed it and already told us
        owner, account = _account_for(d.get("asset_folder"))
        if account is None:
            return set()
        _partial(rtype, dst, str(d.get("asset_folder")).strip("/"), owner, account, d.get("display_name"), d.get("type"))
        return {owner}
    if CloudFile.objects.filter(resource_type=rtype, public_id=dst).exclude(pk=f.pk).exists():
        f.delete()  # the new id is listed already
        return {f.owner_id}
    owners = {f.owner_id}
    if f.url and ("/" + src + ".") in f.url:
        f.url = f.url.replace("/" + src + ".", "/" + dst + ".", 1)
    elif f.url and f.url.endswith("/" + src):
        f.url = f.url[: -len(src)] + dst
    else:
        f.url = _url(rtype, d.get("type"), dst, f.format, f.version)[:1000]
    f.public_id = dst
    if d.get("asset_folder") is not None:
        owner, account = _account_for(d.get("asset_folder"))
        if account is None:
            f.delete()
            return owners
        f.folder, f.owner_id, f.account = str(d["asset_folder"]).strip("/"), owner, account
        owners.add(owner)
    f.save()
    return owners


def _on_move(d):
    owners = set()
    for pid, r in _items(d):
        rtype, pid = r.get("resource_type"), str(pid or "")
        if rtype not in RESOURCE_TYPES or not pid:
            continue
        to = str(r.get("to_asset_folder") or "").strip("/")
        owner, account = _account_for(to)
        f = CloudFile.objects.filter(resource_type=rtype, public_id=pid).first()
        if account is None:
            if f:
                owners.add(f.owner_id)
                f.delete()
            continue
        if f:
            owners.update({f.owner_id, owner})
            f.folder, f.owner_id, f.account = to, owner, account
            f.save()
        else:
            _partial(rtype, pid, to, owner, account, r.get("display_name"), r.get("type"))
            owners.add(owner)
    return owners


def _on_display_name(d):
    owners = set()
    for pid, r in _items(d):
        rtype, pid = r.get("resource_type"), str(pid or r.get("public_id") or "")
        new = str(r.get("new_display_name") or "").strip()
        if rtype not in RESOURCE_TYPES or not pid or not new:
            continue
        f = CloudFile.objects.filter(resource_type=rtype, public_id=pid).first()
        if f is None:
            owner, account = _account_for(r.get("asset_folder"))
            if account:
                _partial(rtype, pid, str(r.get("asset_folder")).strip("/"), owner, account, new, r.get("type"))
                owners.add(owner)
            continue
        ext = f.format or (f.name.rsplit(".", 1)[-1] if "." in f.name else "")
        name = _name(new, pid, ext)
        if name != f.name:
            f.name = name
            f.save(update_fields=["name"])
            owners.add(f.owner_id)
    return owners


def _under(path, prefix):
    return path == prefix or path.startswith(prefix + "/")


def _on_folder_moved(d):
    src = str(d.get("from_path") or "").strip("/")
    dst = str(d.get("to_path") or "").strip("/")
    if not src or not dst:
        return set()
    owners = set()
    for f in CloudFile.objects.filter(folder__startswith=src):
        if not _under(f.folder, src):
            continue
        new = dst + f.folder[len(src):]
        owner, account = _account_for(new)
        owners.add(f.owner_id)
        if account is None:
            f.delete()
            continue
        f.folder, f.owner_id, f.account = new, owner, account
        f.save()
        owners.add(owner)
    for cf in CloudFolder.objects.filter(path__startswith=src):
        if not _under(cf.path, src):
            continue
        new = dst + cf.path[len(src):]
        owner, account = _account_for(new)
        owners.add(cf.account_id)
        if account is None or new == (base_folder() + f"/accounts/{owner}"):
            cf.delete()
            continue
        try:
            with transaction.atomic():
                cf.path, cf.account = new, account
                cf.save()
        except IntegrityError:
            cf.delete()  # already there
        owners.add(owner)
    # The folder itself shows in its new place even before its files are on the list.
    owner, account = _account_for(dst)
    if account and dst != base_folder() + f"/accounts/{owner}":
        CloudFolder.objects.get_or_create(account=account, path=dst)
        owners.add(owner)
    return owners


def _folder_path(d):
    return str(d.get("folder_path") or d.get("path") or "").strip("/")


def _on_folder_created(d):
    path = _folder_path(d)
    owner, account = _account_for(path)
    if account is None or path == base_folder() + f"/accounts/{owner}":
        return set()
    _, created = CloudFolder.objects.get_or_create(account=account, path=path)
    return {owner} if created else set()


def _on_folder_deleted(d):
    path = _folder_path(d)
    if not path:
        return set()
    owners = set()
    for cf in CloudFolder.objects.filter(path__startswith=path):
        if _under(cf.path, path):
            owners.add(cf.account_id)
            cf.delete()
    return owners


HANDLERS = {
    "upload": _on_upload,
    "delete": _on_delete,
    "rename": _on_rename,
    "move": _on_move,
    "resource_display_name_changed": _on_display_name,
    "move_or_rename_asset_folder": _on_folder_moved,
    "move_asset_folder": _on_folder_moved,
    "create_folder": _on_folder_created,
    "create_asset_folder": _on_folder_created,
    "delete_folder": _on_folder_deleted,
    "delete_asset_folder": _on_folder_deleted,
}


# --------------------------------------------------------------------------- the endpoint
@csrf_exempt
@require_http_methods(["POST"])
def notify(request):
    """Cloudinary → POST /app/v1/cloud/notify. 200 when handled or not needed; 401 when the signature or its age
    is wrong (Cloudinary tries again a few times, then gives up)."""
    body = request.body or b""
    if len(body) > MAX_BODY:
        return JsonResponse({"error": "too_large"}, status=413)
    if not signature_ok(body, request.headers.get("X-Cld-Timestamp"), request.headers.get("X-Cld-Signature"), keys()[1]):
        logger.warning("Cloudinary notification refused: bad or old signature")
        return JsonResponse({"error": "bad_signature"}, status=401)
    try:
        d = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"error": "bad_json"}, status=400)
    if not isinstance(d, dict):
        return JsonResponse({"error": "bad_json"}, status=400)
    kind = str(d.get("notification_type") or "")
    handler = HANDLERS.get(kind)
    if handler is None:
        return JsonResponse({"ok": True, "ignored": kind})
    with transaction.atomic():
        owners = handler(d)
    owners = {o for o in owners if o}
    if owners:
        ping_cloud(owners)  # their open Cloud pages reload
    return JsonResponse({"ok": True, "changed": len(owners)})
