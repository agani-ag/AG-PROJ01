"""SyncHub's tools as General links — an open server-to-server API (no user sign-in, no admin login).

SyncHub (AG-PROJ03) holds the web versions of tools SyncUp doesn't build into the app (Hibiscus, J6,
…). It adds each one as a General link, and updates it when its name, address or icon changes:

  POST /app/v1/hub/links   {"key", "ref", "title", "url", "description"?, "icon"?, "color"?,
                            "give_token"?, "active"?}
       → 201 added / 200 updated   {"success": true, "created": bool, "link": {...}}
  GET  /app/v1/hub/links?key=…     → {"success": true, "links": [...]}  (the links this API made)

The only guard is the SyncUp links key (made on Admin → General links); without any key, anyone could
put a link — say a fake bank page — on every SyncUp phone. A link is matched by SyncHub's own `ref`,
kept as the link's external_id, so the same ref updates and a new ref adds. Only fields that are
sent change. SyncHub can hide a link ("active": false) but not delete it, and once the admin
switches a link off it stays off: SyncHub can still change it, but not turn it back on.
"""
import hmac
import re

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from . import link_icons
from .auth import json_body
from .models import AppConfig, AppLink
from .views import _client_ip, _rate_limited

MAX_LINKS = 50
REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,99}$")
_url_ok = URLValidator(schemes=["https"])


def _error(text, status=400):
    return JsonResponse({"success": False, "error": text}, status=status)


def made_here():
    """General links added through this API (SyncHub's)."""
    return AppLink.objects.filter(account__isnull=True, external_id__isnull=False)


def link_json(link):
    return {
        "ref": link.external_id,
        "id": link.id,
        "title": link.title,
        "url": link.url,
        "description": link.description or "",
        "icon": link.icon or "",
        "color": link.color or "",
        "give_token": link.give_token,
        "active": not link.hub_hidden,
        "admin_off": link.admin_off,
        "shown": link.is_active,
        "updated_at": (link.hub_updated_at or link.updated_at).isoformat(),
    }


def _key_error(key):
    expected = AppConfig.load().links_key or ""
    if not expected:
        return _error("No SyncUp links key has been made yet (Admin → General links → New key)", 503)
    if not isinstance(key, str) or not hmac.compare_digest(key.encode(), expected.encode()):
        return _error("A valid key is required", 403)
    return None


@csrf_exempt
@require_http_methods(["GET", "POST"])
def hub_links(request):
    ip = _client_ip(request)
    if _rate_limited(f"hub_links:{ip}", 120, 3600):
        return _error("Too many calls — try again later", 429)
    data = {"key": request.GET.get("key")} if request.method == "GET" else json_body(request)
    if not isinstance(data, dict):
        return _error("Invalid JSON")
    bad = _key_error(data.get("key"))
    if bad:
        return bad
    if request.method == "GET":
        return JsonResponse({"success": True, "links": [link_json(l) for l in made_here().order_by("title")]})
    return _upsert(data)


def _clean(data, creating):
    """The fields sent, checked: ({field: value}, error or None)."""
    out = {}
    if "title" in data or creating:
        title = data.get("title")
        if not isinstance(title, str) or not title.strip():
            return None, "title is required"
        if len(title.strip()) > 100:
            return None, "title must be 100 characters or fewer"
        out["title"] = title.strip()
    if "url" in data or creating:
        url = data.get("url")
        url = url.strip() if isinstance(url, str) else ""
        try:
            _url_ok(url)
        except ValidationError:
            return None, "url must be a full https:// address"
        if len(url) > 500:
            return None, "url must be 500 characters or fewer"
        out["url"] = url
    if "description" in data:
        d = data.get("description")
        if d is not None and not isinstance(d, str):
            return None, "description must be text"
        if d and len(d.strip()) > 200:
            return None, "description must be 200 characters or fewer"
        out["description"] = (d or "").strip() or None
    if "icon" in data:
        icon, err = link_icons.clean_icon(data.get("icon"))
        if err:
            return None, err
        out["icon"] = icon or None
    if "color" in data:
        color, err = link_icons.clean_color(data.get("color"))
        if err:
            return None, err
        out["color"] = color
    for field in ("give_token", "active"):
        if field in data and not isinstance(data.get(field), bool):
            return None, f"{field} must be true or false"
    if "give_token" in data:
        out["give_token"] = data["give_token"]
    if "active" in data:
        out["hub_hidden"] = not data["active"]
    return out, None


def _upsert(data):
    ref = data.get("ref")
    if not isinstance(ref, str) or not REF_RE.match(ref.strip()):
        return _error("ref is required: up to 100 letters, digits and : . _ -")
    ref = ref.strip()
    with transaction.atomic():
        link = made_here().select_for_update().filter(external_id=ref).first()
        creating = link is None
        fields, err = _clean(data, creating)
        if err:
            return _error(err)
        if creating and made_here().count() >= MAX_LINKS:
            return _error(f"Up to {MAX_LINKS} links can be added this way")
        if creating:
            link = AppLink(account=None, external_id=ref)
        for k, v in fields.items():
            setattr(link, k, v)
        link.is_active = not link.hub_hidden and not link.admin_off
        link.hub_updated_at = timezone.now()
        link.save()
    return JsonResponse({"success": True, "created": creating, "link": link_json(link)}, status=201 if creating else 200)
