"""TV station — live TV channels, seeded from iptv-org (management command seed_tv_channels).

The admin keeps it on the "TV station" page (admin_tv_station.py); the app reads it from `catalog`
(GET /app/v1/tv-station, no sign-in). Same version/ETag pattern as home_shortcuts: an app that
already has that version gets 304 and keeps its copy. Logos are hot-linked from iptv-org, not stored.
"""
import hashlib
import json

from django.db.models import Prefetch
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_http_methods

from .models import TvCategory, TvChannel


def catalog_dict():
    """The shown categories, in order, each with its active channels (empty categories left out)."""
    active = TvChannel.objects.filter(is_active=True).order_by("position", "title")
    cats = TvCategory.objects.filter(is_active=True).prefetch_related(Prefetch("channels", queryset=active))
    out = []
    for c in cats:
        items = [{"id": ch.id, "title": ch.title, "logo": ch.logo_url, "url": ch.stream_url} for ch in c.channels.all()]
        if items:
            out.append({"id": c.id, "name": c.name, "channels": items})
    return {"categories": out}


def admin_catalog_dict():
    """Every category and channel, hidden ones included — for the TV station admin page's curation UI."""
    cats = TvCategory.objects.prefetch_related(Prefetch("channels", queryset=TvChannel.objects.order_by("position", "title")))
    out = []
    for c in cats:
        items = [
            {"id": ch.id, "title": ch.title, "logo": ch.logo_url, "url": ch.stream_url, "active": ch.is_active}
            for ch in c.channels.all()
        ]
        out.append({"id": c.id, "name": c.name, "active": c.is_active, "channels": items})
    return {"categories": out}


@require_http_methods(["GET"])
def catalog(request):
    data = catalog_dict()
    version = hashlib.sha1(json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
    etag = f'"{version}"'
    if request.GET.get("v") == version or request.headers.get("If-None-Match") == etag:
        resp = HttpResponse(status=304)
    else:
        resp = JsonResponse({"version": version, **data}, json_dumps_params={"ensure_ascii": False})
    resp["ETag"] = etag
    resp["Cache-Control"] = "no-cache"
    return resp
