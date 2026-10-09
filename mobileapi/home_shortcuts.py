"""Home-page shortcuts — the catalogue every app user sees on the home page, signed in or not.

The admin keeps it on the "Home shortcuts" page (admin_shortcuts.py); the app reads it from `catalog`
(GET /app/v1/shortcuts, no sign-in). The answer carries a `version`: an app that already has that
version gets 304 and keeps its copy. Icons are not stored here — each phone fetches them from the sites.
"""
import hashlib
import json
from urllib.parse import urlparse

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import transaction
from django.db.models import Max, Prefetch
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_http_methods

from .models import HomeShortcut, ShortcutCategory


def catalog_dict():
    """The shown categories, in order, each with its active shortcuts (empty categories left out)."""
    active = HomeShortcut.objects.filter(is_active=True).order_by("position", "title")
    cats = ShortcutCategory.objects.filter(is_active=True).prefetch_related(Prefetch("shortcuts", queryset=active))
    out = []
    for c in cats:
        items = [{"id": s.id, "title": s.title, "url": s.url} for s in c.shortcuts.all()]
        if items:
            out.append({"id": c.id, "name": c.name, "shortcuts": items})
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


# --------------------------------------------------------------------------- #
# Bulk add: "Category, Title, Address" per line (or tab-separated, as pasted from a spreadsheet).
# --------------------------------------------------------------------------- #
_url_ok = URLValidator(schemes=["http", "https"])


def normalize_url(raw):
    """'amazon.in' → 'https://amazon.in'; None when it isn't a web address."""
    u = (raw or "").strip()
    if not u:
        return None
    if "://" not in u:
        u = "https://" + u
    try:
        _url_ok(u)
    except ValidationError:
        return None
    return u if len(u) <= 500 else None


def parse_bulk(text):
    """Lines → ([(category, title, url)], [skipped line texts]). The title may contain commas."""
    rows, skipped = [], []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in (line.split("\t") if "\t" in line else line.split(","))]
        parts = [p for p in parts if p != ""]
        if len(parts) < 2:
            skipped.append(line)
            continue
        url = normalize_url(parts[-1])
        category = parts[0][:60]
        title = ", ".join(parts[1:-1]).strip()[:80] if len(parts) > 2 else ""
        if not url or not category:
            skipped.append(line)
            continue
        if not title:
            title = (urlparse(url).hostname or url).removeprefix("www.")[:80]
        rows.append((category, title, url))
    return rows, skipped


@transaction.atomic
def apply_bulk(rows):
    """Create missing categories and shortcuts; an address already in its category gets the new title.
    Returns (added, updated, new_category_names)."""
    added = updated = 0
    new_categories = []
    cat_pos = (ShortcutCategory.objects.aggregate(m=Max("position"))["m"] or 0)
    by_name = {c.name.lower(): c for c in ShortcutCategory.objects.all()}
    for name, title, url in rows:
        cat = by_name.get(name.lower())
        if cat is None:
            cat_pos += 1
            cat = ShortcutCategory.objects.create(name=name, position=cat_pos)
            by_name[name.lower()] = cat
            new_categories.append(name)
        existing = HomeShortcut.objects.filter(category=cat, url=url).first()
        if existing:
            if existing.title != title:
                existing.title = title
                existing.save(update_fields=["title", "updated_at"])
                updated += 1
            continue
        pos = (cat.shortcuts.aggregate(m=Max("position"))["m"] or 0) + 1
        HomeShortcut.objects.create(category=cat, title=title, url=url, position=pos)
        added += 1
    return added, updated, new_categories
