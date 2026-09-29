"""
Browser sync (/app/v1/browser/sync) — a signed-in user's own Normal-section data across devices.

One request does both directions (a single short request, fine on PythonAnywhere):

  POST browser/sync
  {
    "device_id": "...",
    "since": "<cursor from the last response, or empty for a full download>",
    "changes": [ {"kind": "bookmark|history|shortcut|setting", "key": "<stable id>",
                  "data": {...}, "updated_ms": 1727..., "deleted": false}, ... ],
    "tabs": {"device_name": "Pixel 8", "tabs": [{"title": "...", "url": "..."}]}   # optional
  }
  → {"cursor": "...", "changes": [...items changed on the server since `since`...],
     "other_devices": [{"device_id", "device_name", "updated_at", "tabs": [...]}], "limits": {...}}

The latest change wins (by the phone's `updated_ms`); deletions travel as tombstones. History is
kept 90 days, bookmarks up to 5,000. Work and Incognito never reach this endpoint (the app only
sends Normal data), and the admin sees counts only.
"""
from datetime import timedelta

from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .auth import app_token_required, json_body
from .models import SyncItem
from .views import _bad

HISTORY_DAYS = 90
MAX_BOOKMARKS = 5000
MAX_CHANGES_PER_CALL = 1000
OTHER_DEVICE_DAYS = 30
CLIENT_KINDS = {"bookmark", "history", "shortcut", "setting"}


def _item_dict(i):
    return {
        "kind": i.kind, "key": i.key, "data": i.data,
        "updated_ms": i.updated_ms, "deleted": i.deleted,
    }


@csrf_exempt
@require_http_methods(["POST"])
@app_token_required
def browser_sync(request):
    data = json_body(request)
    if data is None:
        return _bad("Invalid JSON")
    account = request.account
    device_id = (data.get("device_id") or "").strip()[:255]
    changes = data.get("changes") or []
    if not isinstance(changes, list):
        return _bad("changes must be a list")
    if len(changes) > MAX_CHANGES_PER_CALL:
        return _bad(f"Send at most {MAX_CHANGES_PER_CALL} changes per call")

    now = timezone.now()
    since = parse_datetime(data.get("since") or "") if data.get("since") else None
    history_cutoff_ms = int((now - timedelta(days=HISTORY_DAYS)).timestamp() * 1000)
    rejected = []

    with transaction.atomic():
        # ---- apply the phone's changes (latest change wins)
        bookmark_count = SyncItem.objects.filter(account=account, kind="bookmark", deleted=False).count()
        for ch in changes:
            if not isinstance(ch, dict):
                continue
            kind = ch.get("kind")
            key = str(ch.get("key") or "").strip()[:128]
            if kind not in CLIENT_KINDS or not key:
                continue
            try:
                updated_ms = int(ch.get("updated_ms") or 0)
            except (TypeError, ValueError):
                updated_ms = 0
            deleted = bool(ch.get("deleted"))
            if kind == "history" and not deleted and updated_ms < history_cutoff_ms:
                continue  # older than the history window — not kept
            item = SyncItem.objects.filter(account=account, kind=kind, key=key).first()
            if item and item.updated_ms > updated_ms:
                continue  # the server already has a newer change
            if kind == "bookmark" and not deleted and (item is None or item.deleted):
                if bookmark_count >= MAX_BOOKMARKS:
                    rejected.append({"kind": kind, "key": key, "reason": "bookmark limit reached"})
                    continue
                bookmark_count += 1
            if kind == "bookmark" and deleted and item and not item.deleted:
                bookmark_count -= 1
            payload = ch.get("data") if isinstance(ch.get("data"), dict) else {}
            if item is None:
                SyncItem.objects.create(
                    account=account, kind=kind, key=key, data={} if deleted else payload,
                    updated_ms=updated_ms, deleted=deleted, device_id=device_id,
                )
            else:
                item.data = {} if deleted else payload
                item.updated_ms = updated_ms
                item.deleted = deleted
                item.device_id = device_id
                item.save()

        # ---- this device's open Normal tabs (one row per device, replaced each time)
        tabs = data.get("tabs")
        if device_id and isinstance(tabs, dict):
            clean = [
                {"title": str(t.get("title") or "")[:200], "url": str(t.get("url") or "")[:1000]}
                for t in (tabs.get("tabs") or [])[:100] if isinstance(t, dict) and t.get("url")
            ]
            SyncItem.objects.update_or_create(
                account=account, kind="tabs", key=device_id,
                defaults={
                    "data": {"device_name": str(tabs.get("device_name") or "")[:80], "tabs": clean},
                    "updated_ms": int(now.timestamp() * 1000), "deleted": False, "device_id": device_id,
                },
            )

        # ---- trim history past the window (tombstones older than the window are dropped too)
        SyncItem.objects.filter(account=account, kind="history", updated_ms__lt=history_cutoff_ms).delete()

    # ---- what changed on the server since the phone's cursor
    qs = SyncItem.objects.filter(account=account, kind__in=CLIENT_KINDS)
    if since:
        qs = qs.filter(changed_at__gt=since)
    else:
        qs = qs.filter(deleted=False)  # first download: live items only
    out = [_item_dict(i) for i in qs.order_by("changed_at")]
    cursor_row = SyncItem.objects.filter(account=account).order_by("-changed_at").first()
    cursor = (cursor_row.changed_at if cursor_row else now).isoformat()

    recent = now - timedelta(days=OTHER_DEVICE_DAYS)
    others = [
        {
            "device_id": t.key,
            "device_name": (t.data or {}).get("device_name") or "Another device",
            "updated_at": t.changed_at.isoformat(),
            "tabs": (t.data or {}).get("tabs") or [],
        }
        for t in SyncItem.objects.filter(account=account, kind="tabs", changed_at__gte=recent)
        .exclude(key=device_id).order_by("-changed_at")
    ]
    return JsonResponse({
        "success": True,
        "cursor": cursor,
        "changes": out,
        "other_devices": others,
        "rejected": rejected,
        "limits": {"history_days": HISTORY_DAYS, "max_bookmarks": MAX_BOOKMARKS},
    })


@csrf_exempt
@require_http_methods(["POST"])
@app_token_required
def browser_sync_delete(request):
    """Settings → Delete synced data: erases everything synced for this account (this phone keeps
    its local copy)."""
    n = SyncItem.objects.filter(account=request.account).delete()[0]
    return JsonResponse({"success": True, "deleted": n})
