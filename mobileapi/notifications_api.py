"""The app's Notifications page: every push a user (or a signed-out phone) got, in two tabs —
New (not opened yet) and Read — straight from the Push Log (AppNotificationLog).

The list comes from the server, so a push shows up even if the phone blocked notifications, was
offline, or turned "SyncUp updates" off; read state is kept here (AppNotificationRead) so it follows
the user to all their phones. Signed out, a phone sees broadcasts and pushes to that phone only, and
its read state is per phone.

Not listed: chat (it has its own badge), verification codes (codes must not sit in a list) and test
pushes. Broadcasts count only from when the account was created / the app was installed. Rows go
away with the Push Log cleanup (AppConfig.cleanup_log_days, 30 days by default).

  GET  notifications?tab=new|read&before=<id>&limit=30  -> {items, has_more, new_count}
  POST notifications/read  {"ids": [...]} or {"all": true} -> {success, new_count}
  GET  notifications/count                               -> {new_count}

Auth: the usual Bearer token, or — signed out — the phone's X-Device-Id + X-Device-Secret (the
pair device/hello issued).
"""
from datetime import timedelta
from functools import wraps

from django.db.models import Exists, OuterRef, Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .auth import _extract_bearer, json_body
from .models import AppAuthToken, AppDevice, AppNotificationLog, AppNotificationRead

HIDDEN_SOURCES = ("chat", "verification", "test")
PAGE_SIZE = 30
MAX_PAGE_SIZE = 100


def account_or_device_required(view):
    """Signed in: a valid Bearer token (sets request.account). Signed out: the phone's id + secret
    from device/hello (sets request.device). Exactly one of the two is set."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        request.account = request.device = None
        key = _extract_bearer(request)
        if key:
            token = AppAuthToken.objects.select_related("account").filter(key=key).first()
            if not token or not token.is_valid or not token.account.is_active:
                return JsonResponse({"success": False, "message": "Invalid or expired token"}, status=401)
            request.account = token.account
        else:
            device_id = (request.META.get("HTTP_X_DEVICE_ID") or "").strip()
            secret = request.META.get("HTTP_X_DEVICE_SECRET") or ""
            device = AppDevice.objects.filter(device_id=device_id).first() if device_id else None
            if not device or not device.device_secret or device.device_secret != secret:
                return JsonResponse({"success": False, "message": "Authentication required"}, status=401)
            request.device = device
        return view(request, *args, **kwargs)

    return wrapper


def visible(account=None, device=None):
    """Push Log rows this user (account) or signed-out phone (device) can see."""
    from .serializers import live_partner_ids  # avoids an import cycle

    broadcast = Q(account__isnull=True, device__isnull=True)
    if account is not None:
        since = account.created_at
        q = Q(account=account)
        # Push page broadcasts to everyone / signed-in users.
        q |= broadcast & Q(audience__in=("everyone", "signed_in"), sent_at__gte=since)
        # A partner's "notify all" (to its enabled users).
        q |= broadcast & Q(audience="", partner_id__in=live_partner_ids(account), sent_at__gte=since)
        # Sent to all devices / all accounts without a topic (Django admin, scheduled reminders, old rows).
        q |= broadcast & Q(audience="", partner__isnull=True, source__in=("admin", "scheduled", "other"),
                           sent_at__gte=since)
    else:
        since = device.installed_at or (timezone.now() - timedelta(days=365))
        q = Q(device=device)
        q |= broadcast & Q(audience__in=("everyone", "signed_out"), sent_at__gte=since)
        q |= broadcast & Q(audience="", partner__isnull=True, source__in=("admin", "other"), sent_at__gte=since)
    return AppNotificationLog.objects.exclude(source__in=HIDDEN_SOURCES).filter(q)


def _reads(account=None, device=None):
    return AppNotificationRead.objects.filter(**({"account": account} if account is not None else {"device": device}))


def _with_read(qs, account=None, device=None):
    return qs.annotate(is_read=Exists(_reads(account, device).filter(log=OuterRef("pk"))))


def new_count(account=None, device=None):
    """How many visible pushes aren't read yet (the bell's red number)."""
    return _with_read(visible(account, device), account, device).filter(is_read=False).count()


def _kind_and_sender(log):
    data = log.data or {}
    if data.get("type") == "partners":
        return "partners", "Partners"
    if log.source == "scheduled":
        return "reminder", "Reminder"
    if log.site:
        return "site", log.site
    if log.partner_id:
        return "partner", log.partner.name
    if log.source in ("partner_api", "partner_token"):
        return "partner", log.link.title if log.link_id else "Partner"
    return "syncup", "SyncUp"


def item_dict(log):
    data = log.data or {}
    kind, sender = _kind_and_sender(log)
    return {
        "id": str(log.id),
        "title": log.title,
        "body": log.body,
        "image": log.image_url or "",
        "sent_at": log.sent_at.isoformat(),
        "kind": kind,          # syncup | partner | site | reminder | partners
        "sender": sender,      # "SyncUp", the partner's name, the website, …
        "link_url": data.get("link_url") or "",
        "link_title": data.get("link_title") or "",
        "open_in": data.get("open_in") or "",
        "type": data.get("type") or "",
        "read": bool(getattr(log, "is_read", False)),
    }


@require_http_methods(["GET"])
@account_or_device_required
def notifications(request):
    account, device = request.account, request.device
    tab = request.GET.get("tab", "new")
    try:
        limit = max(1, min(MAX_PAGE_SIZE, int(request.GET.get("limit") or PAGE_SIZE)))
    except ValueError:
        limit = PAGE_SIZE
    qs = _with_read(visible(account, device), account, device).filter(is_read=(tab == "read"))
    before = request.GET.get("before")
    if before and before.isdigit():
        qs = qs.filter(id__lt=int(before))
    rows = list(qs.select_related("partner", "link").order_by("-id")[: limit + 1])
    return JsonResponse({
        "items": [item_dict(log) for log in rows[:limit]],
        "has_more": len(rows) > limit,
        "new_count": new_count(account, device),
    })


@csrf_exempt
@require_http_methods(["POST"])
@account_or_device_required
def notifications_read(request):
    account, device = request.account, request.device
    data = json_body(request)
    if not isinstance(data, dict):
        return JsonResponse({"success": False, "message": "Invalid JSON"}, status=400)
    qs = _with_read(visible(account, device), account, device).filter(is_read=False)
    if not data.get("all"):
        ids = [int(i) for i in (data.get("ids") or []) if str(i).isdigit()][:500]
        qs = qs.filter(id__in=ids)
    owner = {"account": account} if account is not None else {"device": device}
    AppNotificationRead.objects.bulk_create(
        [AppNotificationRead(log_id=log_id, **owner) for log_id in qs.values_list("id", flat=True)],
        ignore_conflicts=True,
    )
    return JsonResponse({"success": True, "new_count": new_count(account, device)})


@require_http_methods(["GET"])
@account_or_device_required
def notifications_count(request):
    return JsonResponse({"new_count": new_count(request.account, request.device)})
