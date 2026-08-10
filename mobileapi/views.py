"""
JSON API for the SyncUp Android app (`com.agani.syncup`), namespace /app/v1/.

Function-based, csrf-exempt (token auth, no cookies), matching the existing
project's style. Contract: md/syncup-android-backend-plan.md §5.
"""
from datetime import timedelta

from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .auth import app_token_required, json_body
from .models import AppAccount, AppAuthToken, AppConfig, AppDevice, AppReminder, AppReminderReceipt
from .serializers import account_dict, links_for, reminders_for

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
    """Delete one-time reminders whose time has well passed (safety net for broadcasts and for
    per-account reminders whose device never reported back)."""
    cutoff = timezone.now() - REMINDER_SWEEP_GRACE
    AppReminder.objects.filter(recurrence="once", scheduled_at__lt=cutoff).delete()


@require_http_methods(["GET"])
@app_token_required
def reminders(request):
    _sweep_completed_reminders()
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
        reminder = AppReminder.objects.filter(id=rid).first()
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
@require_http_methods(["GET"])
def config(request):
    cfg = AppConfig.load()
    return JsonResponse({
        "min_supported_version": cfg.min_supported_version,
        "latest_version": cfg.latest_version,
        "support_email": cfg.support_email or "",
        "support_phone": cfg.support_phone or "",
        "privacy_policy_url": _public_url(request, "privacy_policy"),
        "announcement": {
            "active": cfg.announcement_active,
            "title": cfg.announcement_title or "",
            "message": cfg.announcement_message or "",
        },
        "feature_flags": cfg.feature_flags or {},
    })
