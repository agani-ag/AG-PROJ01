"""Media Views - Cloudinary Cloud Config, Status & Gallery.

Endpoints:
  GET  /device/api/cloud-config   - Cloudinary upload config for device
  GET  /device/media/cloud-status - Cloudinary connectivity diagnostics
  GET  /device/media/gallery      - Browse Cloudinary files with download
"""
from __future__ import annotations

import uuid
import logging

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

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
# 3. Cloud Gallery (admin HTML page)
# ====================================================================
GALLERY_PAGE_SIZE = 48


def _list_cloudinary_resources(resource_type, folder, next_cursor=None, max_results=GALLERY_PAGE_SIZE):
    """Fetch resources from Cloudinary Admin API for a given folder."""
    storage_cloud_mod._ensure_configured()
    import cloudinary.api

    params = {
        "type": "upload",
        "prefix": folder,
        "max_results": max_results,
        "direction": -1,
    }
    if next_cursor:
        params["next_cursor"] = next_cursor

    try:
        result = cloudinary.api.resources(resource_type=resource_type, **params)
        return result.get("resources", []), result.get("next_cursor")
    except Exception as e:
        logger.exception("Cloudinary list failed for %s/%s", resource_type, folder)
        return [], None


@require_GET
def cloud_gallery(request):
    """Browse files stored in Cloudinary. Supports folder filter & pagination."""
    if not storage_cloud_mod.is_enabled():
        return render(request, "device_access/cloud_gallery.html", {
            "error": "Cloudinary is not configured.",
        })

    # Folder prefix — default lists the devices folder
    folder_prefix = getattr(settings, "CLOUDINARY_FOLDER_PREFIX", "devices")
    device_filter = request.GET.get("device", "")
    media_filter = request.GET.get("type", "all")  # all, image, video, raw
    cursor = request.GET.get("cursor", "")

    # Build the search prefix
    if device_filter:
        search_folder = f"{folder_prefix}/{device_filter}"
    else:
        search_folder = folder_prefix

    # Fetch resources based on type filter
    items = []
    next_cursor = None

    if media_filter in ("all", "image"):
        imgs, nc = _list_cloudinary_resources("image", search_folder, cursor or None)
        for r in imgs:
            r["_type"] = "image"
            r["_is_image"] = True
        items.extend(imgs)
        if nc:
            next_cursor = nc

    if media_filter in ("all", "video"):
        vids, nc = _list_cloudinary_resources("video", search_folder, cursor or None)
        for r in vids:
            r["_type"] = "video"
            r["_is_image"] = False
        items.extend(vids)
        if nc:
            next_cursor = nc

    if media_filter in ("all", "raw"):
        raws, nc = _list_cloudinary_resources("raw", search_folder, cursor or None)
        for r in raws:
            r["_type"] = "raw"
            r["_is_image"] = False
        items.extend(raws)
        if nc:
            next_cursor = nc

    # Sort by created_at descending
    items.sort(key=lambda r: r.get("created_at", ""), reverse=True)

    # Build display-friendly list
    cloud_name = settings.CLOUDINARY_CLOUD_NAME
    gallery = []
    for r in items:
        public_id = r.get("public_id", "")
        secure_url = r.get("secure_url", "")
        fmt = r.get("format", "")
        rtype = r.get("_type", "raw")
        filename = public_id.rsplit("/", 1)[-1]
        if fmt:
            filename = f"{filename}.{fmt}"

        # Build download URL with fl_attachment flag
        if rtype == "image":
            dl_url = f"https://res.cloudinary.com/{cloud_name}/image/upload/fl_attachment/{public_id}.{fmt}"
        elif rtype == "video":
            dl_url = f"https://res.cloudinary.com/{cloud_name}/video/upload/fl_attachment/{public_id}.{fmt}"
        else:
            dl_url = f"https://res.cloudinary.com/{cloud_name}/raw/upload/fl_attachment/{public_id}"
            if fmt:
                dl_url += f".{fmt}"

        # Thumbnail for images
        thumb_url = ""
        if r.get("_is_image"):
            thumb_url = f"https://res.cloudinary.com/{cloud_name}/image/upload/c_fill,w_300,h_300,q_auto,f_auto/{public_id}.{fmt}"

        # Extract device from folder path
        parts = public_id.split("/")
        device_id = parts[1] if len(parts) >= 3 and parts[0] == folder_prefix else ""

        gallery.append({
            "public_id": public_id,
            "filename": filename,
            "secure_url": secure_url,
            "download_url": dl_url,
            "thumbnail_url": thumb_url,
            "is_image": r.get("_is_image", False),
            "resource_type": rtype,
            "format": fmt,
            "bytes": r.get("bytes", 0),
            "width": r.get("width"),
            "height": r.get("height"),
            "created_at": r.get("created_at", ""),
            "device_id": device_id,
        })

    # Get list of known devices for the filter dropdown
    devices = Device.objects.all().only("id", "user_id", "platform", "device_id")

    return render(request, "device_access/cloud_gallery.html", {
        "gallery": gallery,
        "devices": devices,
        "device_filter": device_filter,
        "media_filter": media_filter,
        "next_cursor": next_cursor or "",
        "cursor": cursor,
        "total_count": len(gallery),
    })


# ====================================================================
# 4. Delete a Cloudinary file (admin action)
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
