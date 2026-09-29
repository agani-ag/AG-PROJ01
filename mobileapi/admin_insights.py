"""
Admin insights (/mobile/): the details behind the Dashboard, Accounts, Devices and account pages.

Devices are shown in full (model, OS, app version, country, switches, sessions). A user's synced
browser data is NEVER shown — only counts and per-device sync state (switches, last sync), in line
with what the app and the privacy policy promise.
"""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone as dt_timezone

from django.conf import settings
from django.contrib.auth.decorators import user_passes_test
from django.db.models import Count, Max, Min
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from .browser_sync import HISTORY_DAYS, MAX_BOOKMARKS, OTHER_DEVICE_DAYS
from .models import (
    AppAccount,
    AppAuthToken,
    AppDevice,
    AppNotificationLog,
    PartnerConnection,
    SyncItem,
)

superuser_required = user_passes_test(
    lambda u: u.is_authenticated and u.is_superuser, login_url=settings.LOGIN_URL
)

CURRENT_APP = 6  # the first app version with sign-up, sync and partners; older installs are v5
SYNC_TYPES = {"bookmarks": "Bookmarks", "history": "History", "tabs": "Open tabs",
              "shortcuts": "Shortcuts", "settings": "Settings"}


def ms_to_dt(ms):
    """The app's millisecond timestamps → aware datetime (None when missing or unreadable)."""
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=dt_timezone.utc) if ms else None
    except (TypeError, ValueError, OverflowError, OSError):
        return None


# --------------------------------------------------------------------------- per-account facts
def device_sync_rows(account):
    """One row per device that has synced for this account (from its 'tabs' item, which every sync
    call rewrites): device name, last sync, the phone's Sync switches and HOW MANY tabs are open.
    The tabs themselves are never passed on."""
    known = {d.device_id: d for d in AppDevice.objects.filter(
        device_id__in=SyncItem.objects.filter(account=account, kind="tabs").values("key"))}
    recent = timezone.now() - timedelta(days=OTHER_DEVICE_DAYS)
    rows = []
    for item in SyncItem.objects.filter(account=account, kind="tabs").order_by("-changed_at"):
        data = item.data or {}
        state = data.get("state")
        rows.append({
            "device_id": item.key,
            "device": known.get(item.key),
            "name": data.get("device_name") or "A device",
            "last_sync": item.changed_at,
            "tab_count": len(data.get("tabs") or []),
            # None = an early v6 build that didn't report its switches yet.
            "enabled": state.get("enabled", True) if isinstance(state, dict) else None,
            "types": [SYNC_TYPES.get(t, t) for t in (state.get("types") or [])] if isinstance(state, dict) else [],
            "recent": item.changed_at >= recent,
        })
    return rows


def sync_summary(account):
    """Counts and date ranges only — for the account page's Browser sync card."""
    live = SyncItem.objects.filter(account=account, deleted=False)
    counts = dict(live.values_list("kind").annotate(n=Count("id")))
    hist = live.filter(kind="history").aggregate(first=Min("updated_ms"), last=Max("updated_ms"))
    shortcuts = live.filter(kind="shortcut", key="user").first()
    hidden = live.filter(kind="shortcut", key="hidden").first()
    bookmarks = counts.get("bookmark", 0)
    devices = device_sync_rows(account)
    return {
        "bookmarks": bookmarks,
        "max_bookmarks": MAX_BOOKMARKS,
        "bookmark_pct": min(100, round(bookmarks * 100 / MAX_BOOKMARKS)) if bookmarks else 0,
        "history": counts.get("history", 0),
        "history_days": HISTORY_DAYS,
        "history_first": ms_to_dt(hist["first"]),
        "history_last": ms_to_dt(hist["last"]),
        "shortcuts": len((shortcuts.data or {}).get("items") or []) if shortcuts else 0,
        "hidden_builtins": len((hidden.data or {}).get("urls") or []) if hidden else 0,
        "settings": counts.get("setting", 0),
        "devices": devices,
        "open_tabs": sum(d["tab_count"] for d in devices),
        "last_change": SyncItem.objects.filter(account=account).aggregate(m=Max("changed_at"))["m"],
        "has_data": bool(counts),
    }


def account_devices(account):
    """The account's installs with their sync state and when they signed in there."""
    sync = {r["device_id"]: r for r in device_sync_rows(account)}
    signed_in = {}
    for t in account.tokens.filter(revoked=False).exclude(device_id="").order_by("created_at"):
        signed_in[t.device_id] = t.created_at  # latest wins
    return [
        {"d": d, "sync": sync.get(d.device_id), "signed_in_at": signed_in.get(d.device_id),
         "current": d.app_version_code >= CURRENT_APP}
        for d in account.devices.defer("fcm_token", "device_secret").order_by("-last_seen")
    ]


def account_activity(account, limit=25):
    """A merged, newest-first timeline of account events: created, sign-ins (with the device),
    partner changes and pushes sent to the account."""
    source = {"self": "Signed up in the app",
              "partner": f"Created by {account.partner.name}" if account.partner_id else "Created by a partner"}
    events = [{"at": account.created_at, "icon": "user-plus", "title": source.get(account.source, "Created by an admin"), "sub": ""}]

    tokens = list(account.tokens.order_by("-created_at")[:15])
    models = dict(AppDevice.objects.filter(device_id__in=[t.device_id for t in tokens if t.device_id])
                  .values_list("device_id", "device_model"))
    for t in tokens:
        where = models.get(t.device_id) or ("a device" if t.device_id else "an older app")
        events.append({"at": t.created_at, "icon": "log-in", "title": "Signed in",
                       "sub": where + (" · session ended" if t.revoked else "")})

    for c in account.partner_connections.select_related("partner"):
        events.append({"at": c.created_at, "icon": "handshake", "title": f"Added by {c.partner.name}", "sub": ""})
        if c.enabled_at and abs((c.enabled_at - c.created_at).total_seconds()) > 5:
            events.append({"at": c.enabled_at, "icon": "circle-check", "title": f"Enabled {c.partner.name}", "sub": "With the partner password"})
        if c.disabled_at:
            events.append({"at": c.disabled_at, "icon": "circle-x", "title": f"Turned off {c.partner.name}", "sub": ""})

    for n in AppNotificationLog.objects.filter(account=account).order_by("-sent_at")[:10]:
        events.append({"at": n.sent_at, "icon": "bell-ring", "title": f"Push: {n.title}", "sub": n.get_source_display()})

    events = [e for e in events if e["at"]]
    events.sort(key=lambda e: e["at"], reverse=True)
    return events[:limit]


# --------------------------------------------------------------------------- lists & dashboard
def account_list_facts():
    """Per-account device and sync facts for the Accounts table (a handful of queries in total)."""
    devices = defaultdict(list)
    for d in AppDevice.objects.filter(account__isnull=False).only(
            "account_id", "app_version", "last_seen", "is_active", "device_model"):
        devices[d.account_id].append(d)
    items = dict(SyncItem.objects.filter(deleted=False).exclude(kind="tabs")
                 .values_list("account_id").annotate(n=Count("id")))
    sync_state = {}
    for row in SyncItem.objects.filter(kind="tabs").order_by("changed_at").values("account_id", "changed_at", "data"):
        state = (row["data"] or {}).get("state")
        sync_state[row["account_id"]] = {  # the most recent device wins
            "last": row["changed_at"],
            "on": state.get("enabled", True) if isinstance(state, dict) else True,
        }
    return devices, items, sync_state


def dashboard_stats():
    now = timezone.now()
    week, month = now - timedelta(days=7), now - timedelta(days=30)

    by_source = dict(AppAccount.objects.values_list("source").annotate(n=Count("id")))
    signups_7 = AppAccount.objects.filter(source="self", created_at__gte=week).count()
    signups_30 = AppAccount.objects.filter(source="self", created_at__gte=month).count()

    # New accounts per day, last 14 days (self sign-ups highlighted).
    today = timezone.localdate()
    days = [today - timedelta(days=i) for i in range(13, -1, -1)]
    per_day, per_day_self = Counter(), Counter()
    for at, src in AppAccount.objects.filter(created_at__date__gte=days[0]).values_list("created_at", "source"):
        d = timezone.localtime(at).date()
        per_day[d] += 1
        if src == "self":
            per_day_self[d] += 1
    peak = max(per_day.values(), default=0) or 1
    chart = [{"day": d, "n": per_day[d], "self": per_day_self[d], "pct": round(per_day[d] * 100 / peak)}
             for d in days]

    devices = list(AppDevice.objects.only(
        "account_id", "app_version", "is_active", "country", "notifications_allowed",
        "updates_enabled", "installed_at", "converted_at"))
    active = [d for d in devices if d.is_active]
    current = [d for d in active if d.app_version_code >= CURRENT_APP]

    syncing = SyncItem.objects.filter(kind="tabs", changed_at__gte=month).values("account_id").distinct().count()
    v6_accounts = len({d.account_id for d in current if d.account_id})

    conn = dict(PartnerConnection.objects.filter(partner_active=True).values_list("status").annotate(n=Count("id")))
    return {
        "by_source": {k: by_source.get(k, 0) for k in ("admin", "partner", "self")},
        "signups_7": signups_7,
        "signups_30": signups_30,
        "chart": chart,
        "chart_total": sum(per_day.values()),
        "installs": len(devices),
        "installs_active": len(active),
        "installs_new_7": sum(1 for d in devices if d.installed_at and d.installed_at >= week),
        "signed_in": sum(1 for d in active if d.account_id),
        "signed_out": sum(1 for d in active if not d.account_id),
        "converted_pct": round(sum(1 for d in devices if d.converted_at) * 100 / (len(devices) or 1)),
        "current_app": len(current),
        "older_app": len(active) - len(current),
        "current_pct": round(len(current) * 100 / (len(active) or 1)),
        "notif_blocked": sum(1 for d in active if d.notifications_allowed is False),
        "updates_off": sum(1 for d in active if not d.updates_enabled),
        "countries": Counter(d.country for d in active if d.country).most_common(6),
        "versions": Counter((d.app_version or "?") for d in active).most_common(6),
        "syncing_accounts": syncing,
        "v6_accounts": v6_accounts,
        "sync_pct": round(syncing * 100 / v6_accounts) if v6_accounts else 0,
        "synced_items": SyncItem.objects.filter(deleted=False).exclude(kind="tabs").count(),
        "partners": {
            "enabled": conn.get("enabled", 0),
            "not_enabled": conn.get("not_enabled", 0),
            "disabled": conn.get("disabled", 0),
            "suspended": PartnerConnection.objects.filter(partner_active=False).count(),
        },
    }


# --------------------------------------------------------------------------- device detail
@superuser_required
def device_detail(request, device_pk):
    """One install: everything it reported, its sign-in sessions and its sync state (counts only)."""
    device = get_object_or_404(AppDevice.objects.select_related("account"), id=device_pk)
    sessions = (AppAuthToken.objects.filter(device_id=device.device_id).select_related("account")
                .order_by("-created_at")[:20])
    sync = None
    if device.account_id:
        sync = next((r for r in device_sync_rows(device.account) if r["device_id"] == device.device_id), None)
    return render(request, "mobileapi/device_detail.html", {
        "device": device,
        "current": device.app_version_code >= CURRENT_APP,
        "sessions": sessions,
        "sync": sync,
        "now": timezone.now(),
    })
