"""
JSON API for the SyncUp Android app (`com.agani.syncup`), namespace /app/v1/.

Function-based, csrf-exempt (token auth, no cookies), matching the existing
project's style. Contract: md/syncup-android-backend-plan.md §5.
"""
import hashlib
import hmac
import json
from datetime import timedelta

import requests
from django.core import signing
from django.core.cache import cache
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from . import fcm
from .auth import app_token_required, json_body
from .models import (
    AppAccount,
    AppActionRequest,
    AppAuthToken,
    AppChatMessage,
    AppConfig,
    AppDevice,
    AppLink,
    AppReminder,
    AppReminderReceipt,
)
from .serializers import PARTNER_NOTIFY_SALT, account_dict, links_for, reminders_for

# Ultimate fallbacks if the (DB-managed) privacy fields are ever blank.
PRIVACY_FALLBACK_COMPANY = "SyncUp"
PRIVACY_FALLBACK_DATE = "10 August 2026"
PRIVACY_FALLBACK_EMAIL = "support@syncup.app"


def _bad(msg, status=400):
    return JsonResponse({"success": False, "message": msg}, status=status)


def _upsert_device(account, device_id, fcm_token, platform="android", app_version=None):
    AppDevice.objects.update_or_create(
        account=account,
        device_id=device_id,
        defaults={
            "fcm_token": fcm_token,
            "platform": platform or "android",
            "app_version": app_version,
            "last_seen": timezone.now(),
            "is_active": True,
        },
    )


# --------------------------------------------------------------------------- #
# 1. Login
# --------------------------------------------------------------------------- #
@csrf_exempt
@require_http_methods(["POST"])
def login(request):
    data = json_body(request)
    if data is None:
        return _bad("Invalid JSON")
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    if not email or not password:
        return _bad("Email and password are required")

    account = AppAccount.objects.filter(email=email).first()
    if not account or not account.check_password(password):
        return _bad("Invalid email or password", status=401)
    if not account.is_active:
        return _bad("Account is inactive", status=403)

    # Optional device registration on login.
    device_id = data.get("device_id")
    fcm_token = data.get("fcm_token")
    if device_id and fcm_token:
        _upsert_device(account, device_id, fcm_token, data.get("platform", "android"), data.get("app_version"))

    account.last_login = timezone.now()
    account.save(update_fields=["last_login"])

    token = AppAuthToken.issue(account)
    return JsonResponse({
        "access_token": token.key,
        "token_type": "Bearer",
        "user": account_dict(account),
        "urls": links_for(account),
    })


# --------------------------------------------------------------------------- #
# 2. Logout
# --------------------------------------------------------------------------- #
@csrf_exempt
@require_http_methods(["POST"])
@app_token_required
def logout(request):
    request.auth_token.revoked = True
    request.auth_token.save(update_fields=["revoked"])
    return JsonResponse({"success": True})


# --------------------------------------------------------------------------- #
# 3. Account links (powers Refresh) — returns a bare array
# --------------------------------------------------------------------------- #
@require_http_methods(["GET"])
@app_token_required
def account_urls(request):
    return JsonResponse(links_for(request.account), safe=False)


# --------------------------------------------------------------------------- #
# 3b. Current account details (name/email) — lets the app pick up admin edits
# --------------------------------------------------------------------------- #
@require_http_methods(["GET"])
@app_token_required
def account_me(request):
    return JsonResponse(account_dict(request.account))


# --------------------------------------------------------------------------- #
# 3a. Self-managed links (only when the account has can_manage_links)
# --------------------------------------------------------------------------- #
@csrf_exempt
@require_http_methods(["POST"])
@app_token_required
def account_link_add(request):
    """Add a link the user created themselves. Returns the refreshed link list."""
    if not request.account.can_manage_links:
        return _bad("You don't have permission to manage links", status=403)
    data = json_body(request)
    if data is None:
        return _bad("Invalid JSON")
    title = (data.get("title") or "").strip()
    url = (data.get("url") or "").strip()
    description = (data.get("description") or "").strip()
    if not title or not url:
        return _bad("Title and URL are required")
    if not url.lower().startswith("https://"):
        return _bad("URL must start with https:// (the in-app browser blocks non-secure links)")
    AppLink.objects.create(
        account=request.account, title=title[:100], url=url[:500],
        description=(description[:200] or None), created_by_user=True, is_active=True,
    )
    return JsonResponse(links_for(request.account), safe=False)


@csrf_exempt
@require_http_methods(["POST"])
@app_token_required
def account_link_delete(request, link_id):
    """Remove a link — only one the caller added themselves. Returns the refreshed list."""
    if not request.account.can_manage_links:
        return _bad("You don't have permission to manage links", status=403)
    link = AppLink.objects.filter(id=link_id, account=request.account, created_by_user=True).first()
    if not link:
        return _bad("Link not found or can't be removed", status=404)
    link.delete()
    return JsonResponse(links_for(request.account), safe=False)


# --------------------------------------------------------------------------- #
# 3b. Partner push — a website (opened in the app) pushes to THIS exact user.
#     Called by the partner's backend with the window.SyncUp.token we injected.
# --------------------------------------------------------------------------- #
PARTNER_NOTIFY_RATE = 20  # max notifications per account per minute


@csrf_exempt
@require_http_methods(["POST"])
def partner_notify(request):
    """POST { token, title, body, url? } — token is the window.SyncUp.token from a link that
    has notifications enabled. Sends an FCM push to that user; tapping opens the link/url.
    No API key: the signed token IS the credential, and unchecking the link's box revokes it."""
    data = json_body(request)
    if data is None:
        return _bad("Invalid JSON")
    token = data.get("token") or ""
    title = (data.get("title") or "").strip()
    body = (data.get("body") or "").strip()
    url = (data.get("url") or "").strip()
    if not token or not title:
        return _bad("token and title are required")
    try:
        payload = signing.loads(token, salt=PARTNER_NOTIFY_SALT)
    except signing.BadSignature:
        return _bad("Invalid token", status=403)
    # The link must still exist, be active, and still have notifications enabled (uncheck = revoke).
    link = (
        AppLink.objects.filter(
            id=payload.get("link_id"), account_id=payload.get("account_id"),
            is_active=True, notify_token_enabled=True,
        )
        .select_related("account").first()
    )
    if not link or not link.account.is_active:
        return _bad("Token revoked or link unavailable", status=403)
    # Simple per-account rate limit.
    rk = f"pnotify:{link.account_id}"
    count = cache.get(rk, 0)
    if count >= PARTNER_NOTIFY_RATE:
        return _bad("Rate limit exceeded, try again shortly", status=429)
    cache.set(rk, count + 1, 60)
    # Deliver via FCM; tap opens the provided https url (must be) or the link itself.
    open_url = url if url.lower().startswith("https://") else link.url
    tokens = list(
        AppDevice.objects.filter(account=link.account, is_active=True)
        .exclude(fcm_token="").exclude(fcm_token__isnull=True)
        .values_list("fcm_token", flat=True)
    )
    sent = 0
    if tokens and fcm.is_configured():
        try:
            sent, _ = fcm.send(tokens, title[:100], body[:200], data={"link_url": open_url, "link_title": link.title})
        except Exception:
            pass
    return JsonResponse({"success": True, "delivered": sent})


# --------------------------------------------------------------------------- #
# 4. Change password
# --------------------------------------------------------------------------- #
@csrf_exempt
@require_http_methods(["POST"])
@app_token_required
def change_password(request):
    data = json_body(request)
    if data is None:
        return _bad("Invalid JSON")
    current = data.get("current_password") or ""
    new = data.get("new_password") or ""
    account = request.account
    if not account.check_password(current):
        return _bad("Current password is incorrect")
    if len(new) < 6:
        return _bad("New password must be at least 6 characters")
    account.set_password(new)
    account.save(update_fields=["password", "updated_at"])
    # Revoke all other tokens; keep the one making this request.
    AppAuthToken.objects.filter(account=account).exclude(id=request.auth_token.id).update(revoked=True)
    return JsonResponse({"success": True})


# --------------------------------------------------------------------------- #
# 5 / 6. Device register / unregister
# --------------------------------------------------------------------------- #
@csrf_exempt
@require_http_methods(["POST"])
@app_token_required
def device_register(request):
    data = json_body(request)
    if data is None:
        return _bad("Invalid JSON")
    device_id = data.get("device_id")
    fcm_token = data.get("fcm_token")
    if not device_id or not fcm_token:
        return _bad("device_id and fcm_token are required")
    _upsert_device(request.account, device_id, fcm_token, data.get("platform", "android"), data.get("app_version"))
    return JsonResponse({"success": True})


@csrf_exempt
@require_http_methods(["POST"])
@app_token_required
def device_unregister(request):
    data = json_body(request)
    if data is None:
        return _bad("Invalid JSON")
    device_id = data.get("device_id")
    if not device_id:
        return _bad("device_id is required")
    AppDevice.objects.filter(account=request.account, device_id=device_id).update(is_active=False)
    return JsonResponse({"success": True})


# --------------------------------------------------------------------------- #
# 6a. Delete account (Play data-deletion requirement) — soft delete
# --------------------------------------------------------------------------- #
@csrf_exempt
@require_http_methods(["POST"])
@app_token_required
def delete_account(request):
    """User-initiated account deletion. Deactivates the account, revokes all tokens, and
    turns off all devices. Admin can restore access later if needed."""
    account = request.account
    account.is_active = False
    account.save(update_fields=["is_active", "updated_at"])
    AppAuthToken.objects.filter(account=account).update(revoked=True)
    AppDevice.objects.filter(account=account).update(is_active=False)
    return JsonResponse({"success": True})


# --------------------------------------------------------------------------- #
# 6b. Reminders — synced to the device, then fired locally by the app
# --------------------------------------------------------------------------- #
# A completed one-time reminder is swept from the backend this long after its scheduled time,
# so every device (incl. broadcast recipients) has had a chance to receive & show it first.
REMINDER_SWEEP_GRACE = timedelta(hours=24)


def _sweep_completed_reminders():
    """Delete finished DEVICE reminders whose time has well passed (safety net for broadcasts and
    for per-account reminders whose device never reported back). Covers one-time reminders and
    'Repeat N times' reminders past their final occurrence.

    Scoped to delivery="device" deliberately. A cron row is deleted by nobody: it's the record of
    a server-side send, and sweeping it on `scheduled_at` alone would destroy a queued push before
    it ever went out whenever the cron fell behind by more than the grace period.
    """
    cutoff = timezone.now() - REMINDER_SWEEP_GRACE
    AppReminder.objects.filter(
        delivery="device", recurrence="once", scheduled_at__lt=cutoff,
    ).delete()
    # Counted reminders: last fire is scheduled_at + (count-1) * interval; sweep once that's well past.
    for r in AppReminder.objects.filter(
        delivery="device", recurrence="interval", repeat_count__gt=0,
    ):
        last = r.scheduled_at + timedelta(seconds=r.repeat_interval_seconds * (r.repeat_count - 1))
        if last < cutoff:
            r.delete()


@require_http_methods(["GET"])
@app_token_required
def reminders(request):
    _sweep_completed_reminders()
    # Record this device's reminder sync (background/foreground) so the admin can see it.
    device_id = request.GET.get("device_id")
    if device_id:
        AppDevice.objects.filter(account=request.account, device_id=device_id).update(
            last_reminder_sync_at=timezone.now(),
        )
    return JsonResponse(reminders_for(request.account), safe=False)


@csrf_exempt
@require_http_methods(["POST"])
@app_token_required
def reminder_ack(request):
    """Device reports delivery: event 'synced' (downloaded/scheduled) or 'fired' (shown).
    Body: { "device_id": "...", "event": "synced|fired", "reminder_ids": ["1","2"] }
    (single "reminder_id" also accepted)."""
    data = json_body(request)
    if data is None:
        return _bad("Invalid JSON")
    event = data.get("event")
    if event not in ("synced", "fired"):
        return _bad("event must be 'synced' or 'fired'")
    device_id = data.get("device_id") or ""
    ids = data.get("reminder_ids")
    if not ids:
        single = data.get("reminder_id")
        ids = [single] if single else []
    if not ids:
        return _bad("reminder_ids is required")

    now = timezone.now()
    updated = 0
    for rid in ids:
        # Only rows this account can actually have been sent: its own or a broadcast, and only
        # device-fired ones (a cron row is never synced, so no device can legitimately ack it).
        # Without the account filter any logged-in account could ack — and thereby delete —
        # another account's reminder.
        reminder = AppReminder.objects.filter(
            Q(account=request.account) | Q(account__isnull=True),
            id=rid, delivery="device",
        ).first()
        if not reminder:
            continue
        receipt, _ = AppReminderReceipt.objects.get_or_create(
            reminder=reminder, account=request.account, device_id=device_id,
        )
        if event == "synced":
            receipt.synced_at = now
            receipt.save()
        else:
            receipt.fired_at = now
            receipt.save()
            # Smart delete: a per-account one-time reminder is done once its device shows it.
            # (Broadcast one-time reminders are swept later by _sweep_completed_reminders.)
            if reminder.recurrence == "once" and reminder.account_id is not None:
                reminder.delete()
        updated += 1
    return JsonResponse({"success": True, "updated": updated})


# --------------------------------------------------------------------------- #
# 6c. Chat (user ↔ admin) — the app opens a web chat page in its WebView
# --------------------------------------------------------------------------- #
@require_http_methods(["GET"])
@app_token_required
def chat_session(request):
    """Mint a short-lived signed link the app opens in its WebView to start a chat session.
    The bearer token stays in the app; the WebView gets only this one-time token."""
    from .chat_views import make_open_token
    token = make_open_token(request.account)
    url = f"{_public_url(request, 'chat_open')}?t={token}"
    return JsonResponse({"url": url})


def _chat_unread_count(account):
    """Chat-badge count. Support agent: messages awaiting reply across all other users.
    Normal user: their own unread admin messages."""
    if account.admin_chat_mode:
        return AppChatMessage.objects.filter(
            sender="user", read_by_admin=False,
        ).exclude(account=account).count()
    return AppChatMessage.objects.filter(
        account=account, sender="admin", read_by_user=False,
    ).count()


@require_http_methods(["GET"])
@app_token_required
def chat_unread(request):
    """Chat button badge. For a support agent: messages awaiting reply across all users.
    For a normal user: their own unread admin messages."""
    return JsonResponse({"count": _chat_unread_count(request.account)})


# --------------------------------------------------------------------------- #
# Public privacy policy page (for the Play Store "Privacy policy" URL)
# --------------------------------------------------------------------------- #
_LOCAL_HOSTS = ("127.0.0.1", "localhost", "10.0.2.2")


def _public_url(request, name):
    """Absolute URL for a view, upgraded to https for real hosts. Django behind the
    Cloudflare tunnel sees the request as http (TLS ends at the tunnel), but the app's
    WebView is https-only — so force https except on local dev hosts."""
    url = request.build_absolute_uri(reverse(name))
    host = request.get_host().split(":")[0]
    if url.startswith("http://") and host not in _LOCAL_HOSTS:
        url = "https://" + url[len("http://"):]
    return url


@require_http_methods(["GET"])
def privacy_policy(request):
    """Public HTML privacy policy. Use this page's URL in Play Console → App content.
    Content is managed in the DB (AppConfig → /mobile/config)."""
    cfg = AppConfig.load()
    return render(request, "mobileapi/privacy.html", {
        "company_name": cfg.privacy_company_name or PRIVACY_FALLBACK_COMPANY,
        "effective_date": cfg.privacy_effective_date or PRIVACY_FALLBACK_DATE,
        "contact_email": cfg.privacy_contact_email or cfg.support_email or PRIVACY_FALLBACK_EMAIL,
        "app_id": "com.agani.syncup",
    })


# --------------------------------------------------------------------------- #
# 7. Server-driven config (unauthenticated)
# --------------------------------------------------------------------------- #
def _config_dict(request):
    cfg = AppConfig.load()
    return {
        "min_supported_version": cfg.min_supported_version,
        "support_email": cfg.support_email or "",
        "support_phone": cfg.support_phone or "",
        "privacy_policy_url": _public_url(request, "privacy_policy"),
        "chat_enabled": cfg.chat_enabled,
        "announcement": {
            "active": cfg.announcement_active,
            "title": cfg.announcement_title or "",
            "message": cfg.announcement_message or "",
            "fullscreen": cfg.announcement_fullscreen,
            "blocking": cfg.announcement_blocking,
        },
        "feature_flags": cfg.feature_flags or {},
    }


@require_http_methods(["GET"])
def config(request):
    return JsonResponse(_config_dict(request))


# --------------------------------------------------------------------------- #
# Partner action / verification — the app fetches the prompt and posts the result.
# --------------------------------------------------------------------------- #
def _action_dict(a):
    return {
        "id": str(a.id),
        "type": a.action_type,
        "title": a.title,
        "message": a.message,
        "params": a.params,
        "status": a.status,
        "expires_at": a.expires_at.isoformat(),
    }


@require_http_methods(["GET"])
@app_token_required
def action_get(request, action_id):
    a = AppActionRequest.objects.filter(id=action_id, account=request.account).first()
    if not a:
        return _bad("Action not found", 404)
    if a.status == "pending" and a.is_expired:
        a.status = "expired"
        a.save(update_fields=["status"])
    return JsonResponse({"success": True, "action": _action_dict(a)})


@csrf_exempt
@require_http_methods(["POST"])
@app_token_required
def action_respond(request, action_id):
    a = (
        AppActionRequest.objects.select_related("partner", "account")
        .filter(id=action_id, account=request.account).first()
    )
    if not a:
        return _bad("Action not found", 404)
    if a.status != "pending":
        return _bad("This action has already been handled", 409)
    if a.is_expired:
        a.status = "expired"
        a.save(update_fields=["status"])
        return _bad("This action has expired", 410)

    data = json_body(request) or {}
    value = data.get("value")
    if a.action_type == "code":
        value = str(value or "").strip()
        if not value:
            return _bad("value (the entered code) is required")
    elif a.action_type == "number":
        value = str(value or "").strip()
        if value not in [str(n) for n in a.params.get("numbers", [])]:
            return _bad("value must be one of the offered numbers")
    elif a.action_type == "approve":
        value = str(value or "").strip().lower()
        if value not in ("approved", "rejected"):
            return _bad("value must be 'approved' or 'rejected'")
    else:  # otp / notice — the user just read the code or message; nothing to capture
        value = "acknowledged"

    a.response = {"value": value}
    a.status = "completed"
    a.completed_at = timezone.now()
    a.save(update_fields=["response", "status", "completed_at"])
    # Admin test prompts have no callback URL — the result is just recorded for the console.
    # Delivered INLINE with a short timeout: PythonAnywhere web apps don't run background threads
    # (they're killed after the request), so a thread here would silently drop every callback.
    if a.callback_url and a.partner_id:
        _deliver_action_callback(a)
    return JsonResponse({"success": True})


def _deliver_action_callback(a):
    """Synchronously POST the (signed) result to the partner's callback URL. Best-effort — if the
    partner's server is momentarily down the delivery is lost and the partner re-requests."""
    responded_at = a.completed_at.isoformat() if a.completed_at else None
    payload = {
        "request_id": str(a.id),
        "type": a.action_type,
        "status": "completed",
        "value": (a.response or {}).get("value"),
        "user": {"id": str(a.account_id), "external_id": a.account.external_id or ""},
        "responded_at": responded_at,
    }
    # A "notice" is read-and-acknowledge — give the partner the plain contract they asked for.
    if a.action_type == "notice":
        payload["acknowledged"] = True
        payload["at"] = responded_at
    # An "approve" is a yes/no decision — surface it as a clean boolean too.
    elif a.action_type == "approve":
        payload["approved"] = (a.response or {}).get("value") == "approved"
        payload["at"] = responded_at
    body = json.dumps(payload).encode()
    sig = hmac.new(
        (a.partner.signing_secret or "").encode(), body, hashlib.sha256,
    ).hexdigest()
    try:
        requests.post(
            a.callback_url, data=body,
            headers={"Content-Type": "application/json", "X-SyncUp-Signature": f"sha256={sig}"},
            timeout=4,  # short — this runs inline, so it must not hold up the app's response
        )
    except requests.RequestException:
        pass


# --------------------------------------------------------------------------- #
# Combined refresh — one call for the app's pull-to-refresh / foreground sync
# --------------------------------------------------------------------------- #
@require_http_methods(["GET"])
@app_token_required
def sync(request):
    """Everything the app refreshes at once: the user's own details (name/email — an admin may
    have edited them), their link list, the chat-badge count, and the server config. Replaces
    four separate GETs. Reminders stay on their own endpoint (they have a scheduling/ack flow)."""
    return JsonResponse({
        "user": account_dict(request.account),
        "urls": links_for(request.account),
        "chat_unread": _chat_unread_count(request.account),
        "config": _config_dict(request),
    })
