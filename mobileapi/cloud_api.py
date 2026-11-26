"""App → Tools → Cloud (Cloudinary, 2026-11-26).

PythonAnywhere can't reach Cloudinary, so this server never calls it. The phone does every Cloudinary call
itself (upload, delete, rename, view); this server only:

- says who may use it — AppAccount.cloudinary_access: "off", "own" (their own folder) or "all" (everything);
- signs what an "Own folder" phone sends to Cloudinary. A signature is plain maths with the API secret (no
  network): the server mixes the request with the secret, Cloudinary does the same maths and accepts the
  request only if both match — so a phone can't change the folder or the file it was signed for;
- hands the API key and secret to "Everything" phones (admins / staff), which then use Cloudinary's full API;
- keeps the list of files and folders in each person's own folder (<CLOUDINARY_FOLDER>/accounts/<id>),
  because listing Cloudinary needs the secret over the network. "Own folder" phones read their folder here.

An "Own folder" phone only ever gets signatures for files in its own folder, so it can't reach anyone else's.
"""
import hashlib
import hmac
import os
import re
import secrets
import time

from django.conf import settings
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .auth import app_token_required, json_body
from .models import AppAccount, CloudFile, CloudFolder, CloudUsage

RESOURCE_TYPES = ("image", "video", "raw")
# One folder name: letters (any language), digits, spaces and a few safe marks. Cloudinary refuses ? & # \ % < > +.
_FOLDER_PART = re.compile(r"^[\w .()'-]{1,80}$")
_IMAGE_EXT = {"jpg", "jpeg", "png", "gif", "webp", "heic", "heif", "bmp", "tif", "tiff", "avif", "svg", "ico"}
_VIDEO_EXT = {"mp4", "mov", "m4v", "webm", "mkv", "avi", "3gp", "mp3", "m4a", "aac", "wav", "ogg", "opus", "flac", "amr"}
MB = 1024 * 1024


# --------------------------------------------------------------------------- config
# Everything comes from the Cloudinary settings already in main/settings.py (read from the env): cloud name,
# API key and secret, and CLOUDINARY_FOLDER. The rest are fixed here.

# Cloudinary's folder mode. Newer accounts are "dynamic"; the Everything app checks the real one and warns.
FOLDER_MODE = "dynamic"
# No space limit per person (user, 26 Nov 2026): everyone shares the Cloudinary plan, and sees how much of it is
# used and still free (cloud_plan). Cloudinary's plan decides the biggest file.
GB = 1024 * 1024 * 1024


def cloud_name():
    return getattr(settings, "CLOUDINARY_CLOUD_NAME", "") or ""


def keys():
    """The API key and secret (main/settings.py)."""
    return getattr(settings, "CLOUDINARY_API_KEY", "") or "", getattr(settings, "CLOUDINARY_API_SECRET", "") or ""


def configured():
    key, secret = keys()
    return bool(cloud_name() and key and secret)


def folder_mode():
    return FOLDER_MODE


def base_folder():
    return (getattr(settings, "CLOUDINARY_FOLDER", "") or "syncup").strip("/")


def account_root(account_id):
    """Someone's own folder: <CLOUDINARY_FOLDER>/accounts/<id>."""
    return f"{base_folder()}/accounts/{account_id}"


def cloud_plan():
    """How much of the Cloudinary plan is used and free, as an Everything admin's app last reported it (this
    server can't ask Cloudinary). None until one has."""
    u = CloudUsage.objects.first()
    if not u or not u.total_bytes:
        return None
    return {
        "plan": u.plan,
        "used_bytes": u.used_bytes,
        "total_bytes": u.total_bytes,
        "free_bytes": max(0, u.total_bytes - u.used_bytes),
        "updated_ms": int(u.updated_at.timestamp() * 1000) if u.updated_at else 0,
    }


def plan_from_cloudinary(d):
    """(plan, used, total, storage) in bytes from Cloudinary's usage report, or None. Plans with a storage limit
    use it; credit plans count 1 credit as 1 GB (of storage, downloads or picture changes together)."""
    if not isinstance(d, dict):
        return None
    storage = d.get("storage") if isinstance(d.get("storage"), dict) else {}
    credits = d.get("credits") if isinstance(d.get("credits"), dict) else {}
    try:
        if storage.get("limit"):
            used, total = float(storage.get("usage") or 0), float(storage["limit"])
        elif credits.get("limit"):
            used, total = float(credits.get("usage") or 0) * GB, float(credits["limit"]) * GB
        else:
            return None
        return str(d.get("plan") or "")[:60], int(used), int(total), int(float(storage.get("usage") or 0))
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- signatures (no network)
def sign(params, secret):
    """Cloudinary's request signature: the parameters sorted by name as key=value, joined with &, the secret
    added at the end, SHA-1 in hex. Empty values are left out, as Cloudinary does."""
    to_sign = "&".join(f"{k}={params[k]}" for k in sorted(params) if params[k] not in (None, ""))
    return hashlib.sha1((to_sign + secret).encode("utf-8")).hexdigest()


def response_signature_ok(public_id, version, signature, secret):
    """An upload result really came from Cloudinary: it signs public_id + version with the secret
    (SHA-1, or SHA-256 when the account is set to it)."""
    if not (public_id and version and signature and secret):
        return False
    payload = f"public_id={public_id}&version={version}{secret}".encode("utf-8")
    return any(hmac.compare_digest(algo(payload).hexdigest(), str(signature)) for algo in (hashlib.sha1, hashlib.sha256))


# --------------------------------------------------------------------------- paths and names
def clean_rel(path):
    """A folder path inside someone's own folder ("Trips/2026"), tidied; None when it isn't allowed."""
    parts = [p.strip() for p in str(path or "").replace("\\", "/").split("/") if p.strip()]
    if len(parts) > 10:
        return None
    for p in parts:
        if p in (".", "..") or not _FOLDER_PART.match(p):
            return None
    return "/".join(parts)


def _stem_ext(name):
    base = os.path.basename(str(name or "").replace("\\", "/")).strip() or "file"
    stem, ext = os.path.splitext(base)
    stem = re.sub(r"[^\w-]+", "_", stem).strip("_")[:60] or "file"
    ext = re.sub(r"[^A-Za-z0-9]", "", ext)[:10].lower()
    return stem, ext


def kind_of(mime, name):
    """Cloudinary's resource type for a file: image, video (sound too) or raw (anything else)."""
    m = str(mime or "").lower()
    if m.startswith("image/"):
        return "image"
    if m.startswith(("video/", "audio/")):
        return "video"
    ext = _stem_ext(name)[1]
    if not m or m == "application/octet-stream":
        if ext in _IMAGE_EXT:
            return "image"
        if ext in _VIDEO_EXT:
            return "video"
    return "raw"


def new_public_id(folder_abs, name, resource_type):
    """<folder>/<name>_<6 random hex>; files that aren't pictures or videos keep their extension in the id."""
    stem, ext = _stem_ext(name)
    pid = f"{folder_abs}/{stem}_{secrets.token_hex(3)}"
    if resource_type == "raw" and ext:
        pid += "." + ext
    return pid


def owner_of(public_id):
    """The account whose own folder a public id is in (<base>/accounts/<id>/...), else None."""
    prefix = base_folder() + "/accounts/"
    pid = str(public_id or "")
    if not pid.startswith(prefix):
        return None
    head = pid[len(prefix):].split("/", 1)
    if len(head) < 2 or not head[0].isdigit():
        return None
    return int(head[0])


def _size(n):
    n = int(n or 0)
    if n >= MB:
        return f"{n / MB:.1f} MB"
    return f"{max(1, round(n / 1024))} KB"


def used_bytes(account_id):
    return CloudFile.objects.filter(owner_id=account_id).aggregate(t=Sum("bytes"))["t"] or 0


def usage_text(account):
    """ "12 files · 34.5 MB" in this account's own folder, or "" when there's nothing (account page)."""
    qs = CloudFile.objects.filter(owner_id=account.id)
    n = qs.count()
    if not n:
        return ""
    return f"{n} file{'' if n == 1 else 's'} · {_size(qs.aggregate(t=Sum('bytes'))['t'] or 0)}"


def file_dict(f):
    root = account_root(f.owner_id)
    folder = f.folder[len(root):].lstrip("/") if f.folder == root or f.folder.startswith(root + "/") else f.folder
    return {
        "public_id": f.public_id,
        "resource_type": f.resource_type,
        "name": f.name,
        "format": f.format,
        "bytes": f.bytes,
        "width": f.width,
        "height": f.height,
        "duration": f.duration,
        "url": f.url,
        "folder": folder,
        "created_ms": int(f.created_at.timestamp() * 1000) if f.created_at else 0,
    }


def _err(code, message, status, **extra):
    return JsonResponse({"error": code, "message": message, **extra}, status=status)


def _gate(request, need_all=False):
    """None when this account may use Cloudinary (need_all: only Everything), else the refusal to send."""
    if not configured():
        return _err("not_configured", "Cloud isn't available yet. Please try again later.", 503)
    access = request.account.cloudinary_access
    if access == "off" or (need_all and access != "all"):
        return _err("forbidden", "Cloud isn't switched on for your account.", 403)
    return None


def _target(request, body):
    """(owner account id, folder path) a request is about. "Own folder": always their own folder, the path
    relative to it. "Everything": a full path ("path") inside someone's own folder, or a relative one ("folder")
    inside their own. None when the path isn't allowed."""
    account = request.account
    full = body.get("path")
    if full is not None and account.cloudinary_access == "all":
        owner = owner_of(str(full).strip("/") + "/x")
        if owner is None:
            return None
        rel = clean_rel(str(full).strip("/")[len(account_root(owner)):])
        if rel is None:
            return None
        root = account_root(owner)
        return owner, (f"{root}/{rel}" if rel else root)
    rel = clean_rel(body.get("folder"))
    if rel is None:
        return None
    root = account_root(account.id)
    return account.id, (f"{root}/{rel}" if rel else root)


# --------------------------------------------------------------------------- endpoints
@require_http_methods(["GET"])
@app_token_required
def access(request):
    """What this account gets: its access level, its own folder, space used and the size limits."""
    a = request.account
    return JsonResponse({
        "access": a.cloudinary_access,
        "configured": configured(),
        "cloud_name": cloud_name(),
        "base": base_folder(),
        "root": account_root(a.id),
        "folder_mode": folder_mode(),
        "used_bytes": used_bytes(a.id),
        "cloud": cloud_plan(),
    })


@csrf_exempt
@require_http_methods(["POST"])
@app_token_required
def credentials(request):
    """Everything accounts only: the API key and secret for Cloudinary's full API on their phone."""
    refused = _gate(request, need_all=True)
    if refused:
        return refused
    key, secret = keys()
    resp = JsonResponse({
        "cloud_name": cloud_name(), "api_key": key, "api_secret": secret,
        "base": base_folder(), "folder_mode": folder_mode(),
    })
    resp["Cache-Control"] = "no-store"
    return resp


@require_http_methods(["GET"])
@app_token_required
def files(request):
    """One folder of someone's own space: its subfolders and its files, from this server's list.
    ?folder=Trips/2026 (relative to their own folder); Everything accounts may pass ?path=<full path> instead."""
    refused = _gate(request)
    if refused:
        return refused
    query = {"folder": request.GET.get("folder", "")}
    if "path" in request.GET:
        query["path"] = request.GET.get("path")
    target = _target(request, query)
    if target is None:
        return _err("bad_folder", "That folder name isn't allowed.", 400)
    owner, base = target
    prefix = base + "/"
    children = set()
    for path in CloudFolder.objects.filter(account_id=owner, path__startswith=prefix).values_list("path", flat=True):
        if path.startswith(prefix):  # SQLite's startswith ignores case; keep exact matches only
            children.add(path[len(prefix):].split("/")[0])
    for path in CloudFile.objects.filter(owner_id=owner, folder__startswith=prefix).values_list("folder", flat=True).distinct():
        if path.startswith(prefix):
            children.add(path[len(prefix):].split("/")[0])
    rows = [f for f in CloudFile.objects.filter(owner_id=owner, folder=base) if f.folder == base]
    return JsonResponse({
        "folder": base[len(account_root(owner)):].lstrip("/"),
        "path": base,
        "folders": sorted(children, key=lambda n: (n.lower(), n)),
        "files": [file_dict(f) for f in rows],
        "used_bytes": used_bytes(owner),
        "cloud": cloud_plan(),
    })


@csrf_exempt
@require_http_methods(["POST"])
@app_token_required
def sign_request(request):
    """A signed Cloudinary request for the phone to send itself.
    {"action": "upload", "folder": "Trips", "name": "IMG_1.jpg", "mime": "image/jpeg", "bytes": 123}
    {"action": "destroy", "public_id": "...", "resource_type": "image"}
    {"action": "rename", "public_id": "...", "resource_type": "image", "name": "Beach.jpg"}
    Answer: {"url": <Cloudinary API address>, "params": {... "signature", "api_key"}} — the phone posts the
    params (and the file) to the url. Only for files in the account's own folder ("path" for Everything)."""
    refused = _gate(request)
    if refused:
        return refused
    body = json_body(request) or {}
    account = request.account
    key, secret = keys()
    api = f"https://api.cloudinary.com/v1_1/{cloud_name()}"
    ts = str(int(time.time()))
    action = body.get("action")

    if action == "upload":
        target = _target(request, body)
        if target is None:
            return _err("bad_folder", "That folder name isn't allowed.", 400)
        owner, folder_abs = target
        rtype = kind_of(body.get("mime"), body.get("name"))
        pid = new_public_id(folder_abs, body.get("name"), rtype)
        params = {"public_id": pid, "timestamp": ts}
        if folder_mode() == "dynamic":
            params["asset_folder"] = folder_abs
        return JsonResponse({
            "url": f"{api}/{rtype}/upload", "resource_type": rtype, "public_id": pid,
            "params": {**params, "signature": sign(params, secret), "api_key": key},
        })

    if action in ("destroy", "rename"):
        rtype = body.get("resource_type")
        pid = str(body.get("public_id") or "")
        f = CloudFile.objects.filter(resource_type=rtype, public_id=pid).first()
        if not f or f.public_id != pid or (account.cloudinary_access != "all" and f.owner_id != account.id):
            return _err("not_found", "That file isn't in your folder.", 404)
        if action == "destroy":
            params = {"public_id": pid, "timestamp": ts, "invalidate": "true"}
            return JsonResponse({"url": f"{api}/{rtype}/destroy",
                                 "params": {**params, "signature": sign(params, secret), "api_key": key}})
        name = str(body.get("name") or "").strip()[:255]
        if not name:
            return _err("bad_name", "Type a name.", 400)
        if rtype == "raw" and not _stem_ext(name)[1]:
            old_ext = _stem_ext(f.public_id)[1]
            if old_ext:
                name = f"{name}.{old_ext}"
        to = new_public_id(f.folder, name, rtype)
        params = {"from_public_id": pid, "to_public_id": to, "timestamp": ts, "invalidate": "true"}
        return JsonResponse({"url": f"{api}/{rtype}/rename", "to_public_id": to, "name": name,
                             "params": {**params, "signature": sign(params, secret), "api_key": key}})

    return _err("bad_action", "Unknown action.", 400)


@csrf_exempt
@require_http_methods(["POST"])
@app_token_required
def uploaded(request):
    """The phone reports a finished upload: {"result": <Cloudinary's answer>, "name": "IMG_1.jpg"}. Its signature
    is checked, so only real uploads are listed. Files outside anyone's own folder (an Everything admin's
    upload elsewhere) aren't listed here — the admin's app sees them through Cloudinary itself."""
    refused = _gate(request)
    if refused:
        return refused
    body = json_body(request) or {}
    r = body.get("result") or {}
    pid, version, rtype = str(r.get("public_id") or ""), r.get("version"), r.get("resource_type")
    if rtype not in RESOURCE_TYPES or not pid:
        return _err("bad_result", "That isn't an upload result.", 400)
    if not response_signature_ok(pid, version, r.get("signature"), keys()[1]):
        return _err("bad_signature", "That upload result isn't signed by Cloudinary.", 400)
    owner = owner_of(pid)
    if owner is None:
        return JsonResponse({"ok": True, "listed": False})
    if request.account.cloudinary_access != "all" and owner != request.account.id:
        return _err("forbidden", "That file isn't in your folder.", 403)

    def num(key, cast=int):
        try:
            return cast(r.get(key)) if r.get(key) is not None else None
        except (TypeError, ValueError):
            return None

    f, _ = CloudFile.objects.update_or_create(
        resource_type=rtype, public_id=pid,
        defaults={
            "account": AppAccount.objects.filter(pk=owner).first(),
            "owner_id": owner,
            "folder": pid.rsplit("/", 1)[0],
            "name": str(body.get("name") or r.get("original_filename") or pid.rsplit("/", 1)[-1])[:255],
            "format": str(r.get("format") or "")[:20],
            "bytes": num("bytes") or 0,
            "width": num("width"),
            "height": num("height"),
            "duration": num("duration", float),
            "version": str(version)[:20],
            "url": str(r.get("secure_url") or r.get("url") or "")[:1000],
            "uploaded_by": request.account,
        },
    )
    return JsonResponse({"ok": True, "listed": True, "file": file_dict(f)})


@csrf_exempt
@require_http_methods(["POST"])
@app_token_required
def deleted(request):
    """The phone deleted files at Cloudinary: take them off the list.
    {"public_id": ..., "resource_type": ...} or {"files": [{...}, ...]}. "Own folder": their own files only."""
    refused = _gate(request)
    if refused:
        return refused
    body = json_body(request) or {}
    items = body.get("files") if isinstance(body.get("files"), list) else [body]
    removed = 0
    for it in items[:500]:
        if not isinstance(it, dict):
            continue
        qs = CloudFile.objects.filter(public_id=str(it.get("public_id") or ""), resource_type=it.get("resource_type"))
        if request.account.cloudinary_access != "all":
            qs = qs.filter(owner_id=request.account.id)
        removed += qs.delete()[0]
    return JsonResponse({"ok": True, "removed": removed})


@csrf_exempt
@require_http_methods(["POST"])
@app_token_required
def renamed(request):
    """The phone renamed a file at Cloudinary: {"from_public_id", "to_public_id", "resource_type", "name",
    "url", "version"}. "Own folder": their own file, kept in the same folder."""
    refused = _gate(request)
    if refused:
        return refused
    body = json_body(request) or {}
    src, dst = str(body.get("from_public_id") or ""), str(body.get("to_public_id") or "")
    f = CloudFile.objects.filter(public_id=src, resource_type=body.get("resource_type")).first()
    if not f or not dst:
        return _err("not_found", "That file isn't on the list.", 404)
    is_all = request.account.cloudinary_access == "all"
    if not is_all and (f.owner_id != request.account.id or dst.rsplit("/", 1)[0] != f.folder):
        return _err("forbidden", "That file isn't in your folder.", 403)
    owner = owner_of(dst)
    if owner is None:  # an admin moved it out of every own folder
        f.delete()
        return JsonResponse({"ok": True, "listed": False})
    f.public_id, f.folder, f.owner_id = dst, dst.rsplit("/", 1)[0], owner
    f.account = AppAccount.objects.filter(pk=owner).first()
    f.name = str(body.get("name") or dst.rsplit("/", 1)[-1])[:255]
    if body.get("url"):
        f.url = str(body["url"])[:1000]
    if body.get("version"):
        f.version = str(body["version"])[:20]
    f.save()
    return JsonResponse({"ok": True, "listed": True, "file": file_dict(f)})


@csrf_exempt
@require_http_methods(["POST"])
@app_token_required
def folder_create(request):
    """A new (empty) folder: {"folder": "Trips/2026"} in their own space ("path" for Everything)."""
    refused = _gate(request)
    if refused:
        return refused
    target = _target(request, json_body(request) or {})
    if target is None:
        return _err("bad_folder", "That folder name isn't allowed.", 400)
    owner, path = target
    if path == account_root(owner):
        return JsonResponse({"ok": True})
    if not AppAccount.objects.filter(pk=owner).exists():
        return _err("not_found", "No such account.", 404)
    CloudFolder.objects.get_or_create(account_id=owner, path=path)
    return JsonResponse({"ok": True})


@csrf_exempt
@require_http_methods(["POST"])
@app_token_required
def folder_delete(request):
    """Remove a folder (and its empty subfolders) from the list — only once its files are deleted."""
    refused = _gate(request)
    if refused:
        return refused
    target = _target(request, json_body(request) or {})
    if target is None:
        return _err("bad_folder", "That folder name isn't allowed.", 400)
    owner, path = target
    if path == account_root(owner):
        return _err("bad_folder", "Your own folder can't be deleted.", 400)
    prefix = path + "/"
    if any(p == path or p.startswith(prefix)
           for p in CloudFile.objects.filter(Q(folder=path) | Q(folder__startswith=prefix), owner_id=owner)
           .values_list("folder", flat=True)):
        return _err("not_empty", "Delete the files in it first.", 409)
    ids = [f.id for f in CloudFolder.objects.filter(account_id=owner, path__startswith=path)
           if f.path == path or f.path.startswith(prefix)]
    CloudFolder.objects.filter(id__in=ids).delete()
    return JsonResponse({"ok": True})


@require_http_methods(["GET"])
@app_token_required
def accounts(request):
    """Everything accounts: the people with an own folder, so the app can show names, not numbers."""
    refused = _gate(request, need_all=True)
    if refused:
        return refused
    ids = set(CloudFile.objects.values_list("owner_id", flat=True).distinct())
    ids |= set(CloudFolder.objects.values_list("account_id", flat=True).distinct())
    people = AppAccount.objects.filter(Q(pk__in=ids) | ~Q(cloudinary_access="off")).order_by("name")
    return JsonResponse({"accounts": [
        {"id": a.id, "name": a.name, "login": a.login_label, "access": a.cloudinary_access, "root": account_root(a.id)}
        for a in people
    ]})


@require_http_methods(["GET"])
@app_token_required
def pending(request):
    """Everything accounts: files of deleted accounts, still at Cloudinary. The admin's app deletes them there,
    then reports them with /cloud/deleted (this server can't reach Cloudinary itself)."""
    refused = _gate(request, need_all=True)
    if refused:
        return refused
    rows = CloudFile.objects.filter(account__isnull=True).order_by("created_at")[:500]
    return JsonResponse({
        "count": CloudFile.objects.filter(account__isnull=True).count(),
        "files": [{"public_id": f.public_id, "resource_type": f.resource_type, "bytes": f.bytes, "owner_id": f.owner_id}
                  for f in rows],
    })


@csrf_exempt
@require_http_methods(["POST"])
@app_token_required
def usage(request):
    """Everything accounts: their app passes on Cloudinary's usage report ({"usage": <its answer>}), so everyone
    can see how much of the plan is used and free."""
    refused = _gate(request, need_all=True)
    if refused:
        return refused
    parsed = plan_from_cloudinary((json_body(request) or {}).get("usage"))
    if parsed is None:
        return _err("bad_usage", "That isn't a usage report.", 400)
    plan, used, total, storage = parsed
    u = CloudUsage.objects.first() or CloudUsage()
    u.plan, u.used_bytes, u.total_bytes, u.storage_bytes = plan, used, total, storage
    u.save()
    return JsonResponse({"ok": True, "cloud": cloud_plan()})
