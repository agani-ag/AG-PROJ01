"""
JSON API for the SyncUp Android app (`com.agani.syncup`), namespace /app/v1/.

Function-based, csrf-exempt (token auth, no cookies), matching the existing
project's style. Contract: md/syncup-android-backend-plan.md §5.
"""
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .auth import app_token_required, json_body
from .models import AppAccount, AppAuthToken, AppConfig, AppDevice
from .serializers import account_dict, links_for


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
# 7. Server-driven config (unauthenticated)
# --------------------------------------------------------------------------- #
@require_http_methods(["GET"])
def config(request):
    cfg = AppConfig.load()
    return JsonResponse({
        "min_supported_version": cfg.min_supported_version,
        "latest_version": cfg.latest_version,
        "support_email": cfg.support_email or "",
        "announcement": {
            "active": cfg.announcement_active,
            "title": cfg.announcement_title or "",
            "message": cfg.announcement_message or "",
        },
        "feature_flags": cfg.feature_flags or {},
    })
