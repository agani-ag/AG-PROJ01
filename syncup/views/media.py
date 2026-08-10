"""Media Views - Cloudinary + ClickSend + Video Player.

Endpoints:
  GET  /device/api/cloud-config      - Cloudinary upload config for a device (mobile)
  GET  /device/media/cloudinary      - Client-side Cloudinary gallery page
  GET  /device/media/clicksend       - Client-side ClickSend SMS page
  GET  /device/media/video-player    - Client-side video player page

The Cloudinary gallery, ClickSend and Video Player pages are fully client-side:
the server only injects config; the browser talks to the third-party APIs directly.
"""
from __future__ import annotations

import json
import base64
import hashlib
import logging
import time

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from django.contrib.auth.decorators import login_required
from django.core.serializers.json import DjangoJSONEncoder

from ..models import Device

logger = logging.getLogger(__name__)


# ====================================================================
# 1. Cloud Config (device -> server) — used by mobile devices
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
# 2. Client-side standalone pages (server only injects config)
# ====================================================================
@require_GET
def cloudinary(request):
    # NOTE: the API secret is NEVER sent to the browser. Signed operations (delete / ZIP)
    # call the server-side `cloud_sign` endpoint instead.
    config = {
        "devices": list(Device.objects.all().values("user_id", "device_id")),
        "upload_preset": getattr(settings, "CLOUDINARY_UPLOAD_PRESET", "syncup_unsigned"),
        "tags": getattr(settings, "CLOUDINARY_FOLDER_PREFIX", "devices"),
        "cloud_name": getattr(settings, "CLOUDINARY_CLOUD_NAME", ""),
        "sign_url": reverse("cloud_sign"),
    }

    encoded = base64.b64encode(json.dumps(config, cls=DjangoJSONEncoder).encode()).decode()
    return render(request, "device_access/cloudinary.html", {"app_config": encoded})


@csrf_exempt
@require_POST
def cloud_sign(request):
    """Sign Cloudinary params server-side so the API secret never reaches the browser.

    Superuser-only. Body: {"params": {...}} → {"signature", "api_key", "timestamp"}.
    The server injects the timestamp; the client echoes the returned timestamp/api_key/signature
    back to Cloudinary's destroy / generate_archive endpoints (existing gallery flow unchanged).
    """
    if not (request.user.is_authenticated and request.user.is_superuser):
        return JsonResponse({"error": "forbidden"}, status=403)
    try:
        body = json.loads(request.body or b"{}")
    except (ValueError, TypeError):
        return JsonResponse({"error": "invalid json"}, status=400)

    params = dict(body.get("params") or {})
    params["timestamp"] = str(int(time.time()))
    to_sign = "&".join(f"{k}={params[k]}" for k in sorted(params) if params[k] not in (None, ""))
    signature = hashlib.sha1((to_sign + settings.CLOUDINARY_API_SECRET).encode()).hexdigest()
    return JsonResponse({
        "signature": signature,
        "api_key": settings.CLOUDINARY_API_KEY,
        "timestamp": params["timestamp"],
    })


@require_GET
def clicksend(request):
    config = {
        "username": getattr(settings, "CLICKSEND_USERNAME", ""),
        "api_key": getattr(settings, "CLICKSEND_API_KEY", ""),
    }

    encoded = base64.b64encode(json.dumps(config, cls=DjangoJSONEncoder).encode()).decode()
    return render(request, "device_access/clicksend.html", {"app_config": encoded})


@login_required
@require_GET
def video_player(request):
    # Paste-and-play only — pure client-side player. Supports YouTube, Vimeo,
    # direct media files (mp4/webm/ogg…) and HLS (.m3u8). No API key, no search,
    # no server-side config.
    return render(request, "device_access/video_player.html")
