"""Media Views - Cloudinary + ClickSend + TV station. Superuser-only, like the rest of AG-PROJ01.

Endpoints:
  GET  /services/cloudinary      - Client-side Cloudinary gallery page
  GET  /services/clicksend       - Client-side ClickSend SMS page
  GET  /services/tv-station      - Client-side video player + a live preview of iptv-org's catalogue
                                    (admin-only testing tool — nothing here reaches the mobile app;
                                    see tv-station-plan.md for why)
  POST /services/cloud-sign      - Server-side signature for the gallery's delete / ZIP calls

The Cloudinary gallery, ClickSend and TV station pages are fully client-side: the server only
injects config/data; the browser does the rest. TV station has no database behind it — every page
load fetches iptv-org's current channels/streams/logos/blocklist live, so it's never stale, but it
also means there's no admin curation (on/off) to persist: this is a browse-and-test-play tool only.
"""
from __future__ import annotations

import json
import base64
import hashlib
import logging
import time

import requests

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from django.core.serializers.json import DjangoJSONEncoder

from .auth import superuser_required

logger = logging.getLogger(__name__)

# ---- TV station: live from iptv-org, no database (see module docstring) ----
IPTV_API_BASE = "https://iptv-org.github.io/api/"

# (display name, iptv-org category slug, iptv-org country code) — exactly one of slug/country is set.
TV_CATEGORIES = [
    ("India", None, "IN"),
    ("News", "news", None),
    ("Movies", "movies", None),
    ("Entertainment", "entertainment", None),
    ("Music", "music", None),
    ("Kids", "kids", None),
    ("Documentary", "documentary", None),
    ("Science", "science", None),
    ("Comedy", "comedy", None),
    # "Sports" deliberately left out — iptv-org's sports channels are the category most likely to
    # carry live-rights content a broadcaster hasn't licensed; this stays an admin preview tool, but
    # no sense building the habit of browsing the highest-risk category here either.
]
TV_LIMIT_DEFAULT = 60
TV_LIMIT_COUNTRY = 120


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


def _fetch_iptv_json(path):
    resp = requests.get(IPTV_API_BASE + path, timeout=20, headers={"User-Agent": "SyncUp-admin/1"})
    resp.raise_for_status()
    return resp.json()


def _iptv_catalog_dict():
    """Live snapshot of iptv-org's catalogue, filtered the same way seed_tv_channels used to: no
    blocklisted/closed/NSFW channels, no channel with no known stream. Fetched fresh every call —
    no caching, no database, so it's never stale (at the cost of a couple of seconds per page load)."""
    channels = _fetch_iptv_json("channels.json")
    streams = _fetch_iptv_json("streams.json")
    logos = _fetch_iptv_json("logos.json")
    blocklist = _fetch_iptv_json("blocklist.json")

    blocked = {b["channel"] for b in blocklist if b.get("channel")}
    stream_by_channel = {}
    for s in streams:
        cid = s.get("channel")
        if cid and s.get("url") and cid not in stream_by_channel:
            stream_by_channel[cid] = s["url"]
    logo_by_channel = {}
    for logo in logos:
        cid = logo.get("channel")
        if cid and logo.get("url") and (cid not in logo_by_channel or logo.get("in_use")):
            logo_by_channel[cid] = logo["url"]

    def eligible(ch):
        return ch["id"] not in blocked and not ch.get("closed") and not ch.get("is_nsfw") and ch["id"] in stream_by_channel

    candidates = [c for c in channels if eligible(c)]

    out = []
    for display_name, slug, country in TV_CATEGORIES:
        if slug:
            picked = [c for c in candidates if slug in (c.get("categories") or [])]
        else:
            picked = [c for c in candidates if c.get("country") == country]
        picked.sort(key=lambda c: c["id"])
        picked = picked[: (TV_LIMIT_COUNTRY if country else TV_LIMIT_DEFAULT)]
        if not picked:
            continue
        out.append({
            "id": display_name,
            "name": display_name,
            "channels": [
                {"id": c["id"], "title": c.get("name") or c["id"], "logo": logo_by_channel.get(c["id"], ""), "url": stream_by_channel[c["id"]]}
                for c in picked
            ],
        })
    return {"categories": out}


@superuser_required
@require_GET
def tv_station(request):
    # The player itself is pure client-side (YouTube, Vimeo, direct media files, HLS .m3u8 via
    # hls.js) — paste any link and it plays. The TV station panel adds a second source: a live
    # preview of iptv-org's catalogue, fetched fresh on every visit (no database — see module
    # docstring for why this stays admin-only and isn't piped to the mobile app).
    try:
        catalog = _iptv_catalog_dict()
    except Exception:
        logger.exception("Couldn't fetch iptv-org's catalogue")
        catalog = {"categories": []}
    catalog_json = json.dumps(catalog, ensure_ascii=False).replace("<", "\\u003c")
    return render(request, "services/video_player.html", {"tv_catalog_json": catalog_json})
