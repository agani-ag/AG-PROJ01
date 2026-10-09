"""Media Views - Cloudinary + ClickSend + TV station. Superuser-only, like the rest of AG-PROJ01.

Endpoints:
  GET  /services/cloudinary      - Client-side Cloudinary gallery page
  GET  /services/clicksend       - Client-side ClickSend SMS page
  GET  /services/tv-station      - Client-side video player + TV station catalogue (paste a link, or
                                    browse/curate the channels seeded from iptv-org — see mobileapi.tv_station)
  POST /services/tv-station/<id>/toggle               - show/hide one channel
  POST /services/tv-station/categories/<id>/toggle    - show/hide a whole category
  POST /services/cloud-sign      - Server-side signature for the gallery's delete / ZIP calls

The Cloudinary gallery, ClickSend and TV station pages are fully client-side: the server only
injects config/data; the browser does the rest (TV station's catalogue toggles are the one exception,
small JSON POSTs back to this app, since they write to the database).
"""
from __future__ import annotations

import json
import base64
import hashlib
import logging
import time

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from django.core.serializers.json import DjangoJSONEncoder

from .auth import superuser_required

logger = logging.getLogger(__name__)


# ====================================================================
# Client-side standalone pages (server only injects config)
# ====================================================================
@superuser_required
@require_GET
def cloudinary(request):
    # NOTE: the API secret is NEVER sent to the browser. Signed operations (delete / ZIP)
    # call the server-side `cloud_sign` endpoint instead.
    config = {
        "upload_preset": getattr(settings, "CLOUDINARY_UPLOAD_PRESET", "syncup_unsigned"),
        "tags": getattr(settings, "CLOUDINARY_FOLDER_PREFIX", "devices"),
        "cloud_name": getattr(settings, "CLOUDINARY_CLOUD_NAME", ""),
        "sign_url": reverse("cloud_sign"),
    }

    encoded = base64.b64encode(json.dumps(config, cls=DjangoJSONEncoder).encode()).decode()
    return render(request, "services/cloudinary.html", {"app_config": encoded})


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


@superuser_required
@require_GET
def clicksend(request):
    config = {
        "username": getattr(settings, "CLICKSEND_USERNAME", ""),
        "api_key": getattr(settings, "CLICKSEND_API_KEY", ""),
    }

    encoded = base64.b64encode(json.dumps(config, cls=DjangoJSONEncoder).encode()).decode()
    return render(request, "services/clicksend.html", {"app_config": encoded})


@superuser_required
@require_GET
def tv_station(request):
    # The player itself is pure client-side (YouTube, Vimeo, direct media files, HLS .m3u8 via
    # hls.js) — paste any link and it plays. The TV station panel adds a second source: the
    # curated channel catalogue from mobileapi.tv_station (seeded from iptv-org), injected here
    # as JSON so the page can browse/play/toggle it without a round trip for every click.
    from mobileapi.tv_station import admin_catalog_dict

    catalog_json = json.dumps(admin_catalog_dict(), ensure_ascii=False).replace("<", "\\u003c")
    return render(request, "services/video_player.html", {"tv_catalog_json": catalog_json})


@superuser_required
@require_POST
def tv_channel_toggle(request, channel_id):
    from mobileapi.models import TvChannel

    ch = get_object_or_404(TvChannel, id=channel_id)
    ch.is_active = not ch.is_active
    ch.save(update_fields=["is_active", "updated_at"])
    return JsonResponse({"ok": True, "active": ch.is_active})


@superuser_required
@require_POST
def tv_category_toggle(request, category_id):
    from mobileapi.models import TvCategory

    cat = get_object_or_404(TvCategory, id=category_id)
    cat.is_active = not cat.is_active
    cat.save(update_fields=["is_active", "updated_at"])
    return JsonResponse({"ok": True, "active": cat.is_active})
