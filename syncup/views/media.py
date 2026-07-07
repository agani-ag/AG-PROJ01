"""Media Views - Cloudinary Cloud Config, Status & Gallery.

Endpoints:
  GET  /device/api/cloud-config   - Cloudinary upload config for device
  GET  /device/media/cloud-status - Cloudinary connectivity diagnostics
  GET  /device/media/gallery      - Browse Cloudinary files with download
"""
from __future__ import annotations

import uuid
import json
import base64
import logging

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET
from django.core.serializers.json import DjangoJSONEncoder

from ..models import Device
from .. import storage_cloud as storage_cloud_mod

logger = logging.getLogger(__name__)


# ====================================================================
# 1. Cloud Config (device -> server)
# ====================================================================
@csrf_exempt
@require_GET
def cloud_config(request):
    """Return Cloudinary upload config for the requesting device.

    The app calls this after login and refreshes every ~1 hour in foreground.
    Response controls whether backup is active and where files land.
    """
    device_id = request.GET.get("device_id") or request.headers.get("X-Device-Id", "")
    user_id = request.GET.get("user_id") or request.headers.get("X-User-Id", "")

    if not device_id:
        return JsonResponse({"error": "device_id required"}, status=400)

    # Verify the device exists
    device = Device.objects.filter(device_id=device_id, is_active=True).first()
    if not device:
        return JsonResponse({"error": "Unknown or inactive device"}, status=401)

    media_types = []
    if device.sync_image:
        media_types.append("image")
    if device.sync_video:
        media_types.append("video")
    if device.sync_audio:
        media_types.append("audio")
    if device.sync_disabled:
        return JsonResponse({"enabled": False, "message": "Sync is disabled for this device"})

    enabled = getattr(settings, "CLOUDINARY_BACKUP_ENABLED", False)

    if not enabled or not getattr(settings, "CLOUDINARY_CLOUD_NAME", None):
        return JsonResponse({"enabled": False})

    return JsonResponse({
        "cloud_name": settings.CLOUDINARY_CLOUD_NAME,
        "upload_preset": getattr(settings, "CLOUDINARY_UPLOAD_PRESET", "syncup_unsigned"),
        "folder_prefix": getattr(settings, "CLOUDINARY_FOLDER_PREFIX", "devices"),
        "max_file_size": getattr(settings, "CLOUDINARY_MAX_FILE_SIZE", 10485760),
        "media_types": media_types,
        "enabled": True,
    })


# ====================================================================
# 2. Cloud Status / Smoke Test (diagnostics)
# ====================================================================
@require_GET
def cloud_status(request):
    """Quick diagnostics: is Cloudinary enabled? Try a tiny upload."""
    storage_cloud = storage_cloud_mod
    info = {
        "CLOUDINARY_CLOUD_NAME": getattr(settings, "CLOUDINARY_CLOUD_NAME", None),
        "CLOUDINARY_FOLDER": getattr(settings, "CLOUDINARY_FOLDER", None),
        "has_api_key": bool(getattr(settings, "CLOUDINARY_API_KEY", None)),
        "has_api_secret": bool(getattr(settings, "CLOUDINARY_API_SECRET", None)),
        "is_enabled": storage_cloud.is_enabled(),
    }
    if not info["is_enabled"]:
        info["error"] = "Cloud not enabled. Did you restart the server after editing .env?"
        return JsonResponse(info, status=400)

    if request.GET.get("test") == "1":
        try:
            test_key = f"_diag/probe_{uuid.uuid4().hex[:8]}.txt"
            url, public_id = storage_cloud.upload_bytes(
                b"cloudinary smoke test", test_key, "text/plain"
            )
            info["test_upload"] = {"key": test_key, "url": url, "public_id": public_id}
            storage_cloud.delete_key(public_id)
            info["test_deleted"] = True
        except Exception as e:
            logger.exception("Cloud smoke test failed")
            info["test_error"] = str(e)
            return JsonResponse(info, status=500)
    return JsonResponse(info)


# ====================================================================
# 3. Cloud Gallery (admin HTML page + AJAX API)
# ====================================================================
GALLERY_API_PAGE = 500  # Cloudinary max per call


@require_GET
def cloud_gallery(request):
    """Render gallery page shell — data loaded via AJAX (cloud_gallery_api)."""
    if not storage_cloud_mod.is_enabled():
        return render(request, "device_access/cloud_gallery.html", {
            "error": "Cloudinary is not configured.",
        })

    devices = Device.objects.all().only("id", "user_id", "platform", "device_id")
    return render(request, "device_access/cloud_gallery.html", {
        "devices": devices,
        "device_filter": request.GET.get("device", ""),
        "media_filter": request.GET.get("type", "image"),
    })

@require_GET
def cloudinary(request):
    config = {
        "devices": list(Device.objects.all().values("user_id", "device_id")),
        "upload_preset": getattr(settings, "CLOUDINARY_UPLOAD_PRESET", "syncup_unsigned"),
        "api_secret": getattr(settings, "CLOUDINARY_API_SECRET", ""),
        "api_key": getattr(settings, "CLOUDINARY_API_KEY", ""),
        "tags": getattr(settings, "CLOUDINARY_FOLDER_PREFIX", "devices"),
        "cloud_name": getattr(settings, "CLOUDINARY_CLOUD_NAME", ""),
    }

    encoded = base64.b64encode(json.dumps(config, cls=DjangoJSONEncoder).encode()).decode()
    return render(request,"device_access/cloudinary.html",{"app_config": encoded})

@require_GET
def clicksend(request):
    config = {
        "username": getattr(settings, "CLICKSEND_USERNAME", ""),
        "api_key": getattr(settings, "CLICKSEND_API_KEY", ""),
    }

    encoded = base64.b64encode(json.dumps(config, cls=DjangoJSONEncoder).encode()).decode()
    return render(request,"device_access/clicksend.html",{"app_config": encoded})

@require_GET
def cloud_gallery_api(request):
    """Return one page of Cloudinary resources as JSON."""
    if not storage_cloud_mod.is_enabled():
        return JsonResponse({"error": "Cloud not configured"}, status=400)

    folder_prefix = getattr(settings, "CLOUDINARY_FOLDER_PREFIX", "devices")
    device_filter = request.GET.get("device", "")
    media_filter = request.GET.get("type", "image")
    cursor = request.GET.get("cursor", "")

    search_folder = f"{folder_prefix}/{device_filter}" if device_filter else ""

    resource_type = media_filter if media_filter in ("image", "video", "raw") else "image"
    batch, next_cursor = storage_cloud_mod.list_resources(
        resource_type, search_folder,
        next_cursor=cursor or None,
        max_results=GALLERY_API_PAGE,
    )

    cloud_name = settings.CLOUDINARY_CLOUD_NAME
    items = []
    total_bytes = 0

    for r in batch:
        public_id = r.get("public_id", "")
        fmt = r.get("format", "")
        display_name = r.get("display_name", "")
        filename = display_name or public_id.rsplit("/", 1)[-1]
        if fmt and not filename.endswith(f".{fmt}"):
            filename = f"{filename}.{fmt}"

        size = r.get("bytes", 0)
        total_bytes += size

        is_image = resource_type == "image"
        if is_image:
            dl_url = f"https://res.cloudinary.com/{cloud_name}/image/upload/fl_attachment/{public_id}.{fmt}"
            thumb = f"https://res.cloudinary.com/{cloud_name}/image/upload/c_fill,w_300,h_300,q_auto,f_auto/{public_id}.{fmt}"
        elif resource_type == "video":
            dl_url = f"https://res.cloudinary.com/{cloud_name}/video/upload/fl_attachment/{public_id}.{fmt}"
            thumb = ""
        else:
            dl_url = f"https://res.cloudinary.com/{cloud_name}/raw/upload/fl_attachment/{public_id}"
            if fmt:
                dl_url += f".{fmt}"
            thumb = ""

        path_parts = public_id.split("/")
        item_device_id = path_parts[1] if len(path_parts) >= 3 and path_parts[0] == folder_prefix else ""

        items.append({
            "public_id": public_id,
            "asset_id": r.get("asset_id", ""),
            "filename": filename,
            "display_name": display_name,
            "secure_url": r.get("secure_url", ""),
            "url": r.get("url", ""),
            "download_url": dl_url,
            "thumbnail_url": thumb,
            "is_image": is_image,
            "resource_type": resource_type,
            "upload_type": r.get("type", ""),
            "format": fmt,
            "version": r.get("version"),
            "asset_folder": r.get("asset_folder", ""),
            "bytes": size,
            "width": r.get("width"),
            "height": r.get("height"),
            "created_at": r.get("created_at", ""),
            "device_id": item_device_id,
        })

    return JsonResponse({
        "items": items,
        "next_cursor": next_cursor or "",
        "count": len(items),
        "total_bytes": total_bytes,
    })


@require_GET
def cloud_file_info_api(request):
    """Return full Cloudinary metadata for one file."""
    if not storage_cloud_mod.is_enabled():
        return JsonResponse({"error": "Cloud not configured"}, status=400)

    public_id = (request.GET.get("public_id") or "").strip()
    resource_type = (request.GET.get("resource_type") or "image").strip()
    if not public_id:
        return JsonResponse({"error": "public_id required"}, status=400)
    if resource_type not in ("image", "video", "raw"):
        resource_type = "image"

    info = storage_cloud_mod.get_resource_info(public_id, resource_type=resource_type)
    if not info:
        return JsonResponse({"error": "Failed to fetch file info"}, status=502)

    return JsonResponse({"success": True, "info": info})


# ====================================================================
# 4. Remove duplicate Cloudinary files
# ====================================================================
@csrf_exempt
def cloud_remove_duplicates(request):
    """Scan all images, find duplicates, delete newer copies.

    Matching modes (via JSON body):
      strict     — same display_name + bytes + dimensions
      medium     — same display_name + dimensions
      name       — same display_name only (keeps largest)
      cloudinary — Cloudinary AI Duplicate Detection add-on (threshold 0-1)
    """
    if request.method != "POST":
        return JsonResponse({"success": False, "message": "POST required"}, status=405)

    import json
    from collections import defaultdict

    mode = "name"
    threshold = 0.8
    try:
        body = json.loads(request.body) if request.body else {}
        mode = body.get("mode", mode)
        threshold = float(body.get("threshold", threshold))
    except (json.JSONDecodeError, ValueError):
        pass

    # Fetch all images
    all_res = []
    cursor = None
    while True:
        batch, cur = storage_cloud_mod.list_resources(
            "image", "", next_cursor=cursor, max_results=GALLERY_API_PAGE,
        )
        all_res.extend(batch)
        if not cur or not batch:
            break
        cursor = cur

    # ── Cloudinary AI mode ──────────────────────────────────────
    if mode == "cloudinary":
        duplicates = []
        errors = []
        for r in all_res:
            pid = r.get("public_id", "")
            try:
                resp = storage_cloud_mod.check_duplicate(pid, threshold)
            except Exception as e:
                errors.append({"public_id": pid, "error": str(e)})
                continue

            if resp.get("error"):
                errors.append({"public_id": pid, "error": str(resp)})
                continue

            moderation = resp.get("moderation", [])
            for m in moderation:
                if m.get("kind") == "duplicate" and m.get("status") == "rejected":
                    dup_of = ""
                    m_resp = m.get("response", {})
                    if isinstance(m_resp, dict):
                        dup_of = m_resp.get("duplicate_of", "")
                    duplicates.append({"public_id": pid, "duplicate_of": dup_of})
                    break

        deleted = []
        failed = []
        for d in duplicates:
            pid = d["public_id"]
            if storage_cloud_mod.delete_key(pid):
                deleted.append(pid)
            else:
                failed.append(pid)

        return JsonResponse({
            "success": True,
            "mode": "cloudinary",
            "threshold": threshold,
            "scanned": len(all_res),
            "duplicate_groups": len(duplicates),
            "deleted": deleted,
            "deleted_count": len(deleted),
            "failed": failed,
            "failed_count": len(failed),
            "errors": errors[:10],
        })

    # ── Name / size / dims modes ────────────────────────────────
    by_fp = defaultdict(list)
    for r in all_res:
        name = r.get("display_name", "") or r.get("public_id", "").rsplit("/", 1)[-1]
        if mode == "strict":
            fp = f"{name}|{r.get('bytes', 0)}|{r.get('width', 0)}x{r.get('height', 0)}"
        elif mode == "medium":
            fp = f"{name}|{r.get('width', 0)}x{r.get('height', 0)}"
        else:  # name
            fp = name
        by_fp[fp].append(r)

    dupes = {k: v for k, v in by_fp.items() if len(v) > 1}
    deleted = []
    failed = []
    for fp, items in dupes.items():
        items.sort(key=lambda r: r.get("bytes", 0), reverse=True)
        for r in items[1:]:
            pid = r.get("public_id", "")
            if storage_cloud_mod.delete_key(pid):
                deleted.append(pid)
            else:
                failed.append(pid)

    return JsonResponse({
        "success": True,
        "mode": mode,
        "scanned": len(all_res),
        "duplicate_groups": len(dupes),
        "deleted": deleted,
        "deleted_count": len(deleted),
        "failed": failed,
        "failed_count": len(failed),
    })


# ====================================================================
# 5. Delete a Cloudinary file (admin action)
# ====================================================================
@csrf_exempt
def cloud_delete_file(request):
    """Delete a file from Cloudinary by public_id."""
    if request.method != "POST":
        return JsonResponse({"success": False, "message": "POST required"}, status=405)

    import json
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"success": False, "message": "Invalid JSON"}, status=400)

    public_id = data.get("public_id", "").strip()
    if not public_id:
        return JsonResponse({"success": False, "message": "public_id required"}, status=400)

    ok = storage_cloud_mod.delete_key(public_id)
    if ok:
        return JsonResponse({"success": True, "message": f"Deleted: {public_id}"})
    return JsonResponse({"success": False, "message": f"Failed to delete: {public_id}"}, status=500)
