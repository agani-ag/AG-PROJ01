"""
Section 4 app endpoints (/app/v1/): self sign-up, profile identifiers, install registry ("device
hello") and the Partners page. Same conventions as views.py: function views, JSON in/out,
hand-rolled Bearer auth, csrf-exempt.
"""
import secrets
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .auth import _extract_bearer, app_token_required, json_body
from .identity import normalize_email, normalize_phone, normalize_username
from .models import (
    PARTNER_LOCK_MINUTES,
    PARTNER_MAX_ATTEMPTS,
    AppAccount,
    AppAuthToken,
    AppConfig,
    AppDevice,
    PartnerConnection,
)
from .serializers import account_dict
from .views import _bad, _client_ip, _issue_session, _rate_limited

TAKEN = {
    "email": "This email is already registered. Sign in instead.",
    "phone": "This phone number is already registered. Sign in instead.",
    "username": "This username is already taken",
}


# --------------------------------------------------------------------------- sign-up
@csrf_exempt
@require_http_methods(["POST"])
def signup(request):
    """Create a self sign-up account and sign it in (same response as login).

    Body: {name, password, email?, phone?, accept_privacy: true, device_id?, fcm_token?, app_version?}
    At least one of email/phone; each must be unused. No verification (by design). A new account
    gets no SyncUp extras (general links, chat, radio) until the admin turns them on."""
    if not AppConfig.load().signup_enabled:
        return _bad("Creating accounts is turned off right now", status=403)
    data = json_body(request)
    if data is None:
        return _bad("Invalid JSON")
    if _rate_limited(f"signup:{_client_ip(request)}", 5, 3600):
        return _bad("Too many sign-ups from this network. Try again later.", status=429)

    name = (data.get("name") or "").strip()
    password = data.get("password") or ""
    raw_email = (data.get("email") or "").strip()
    raw_phone = (data.get("phone") or "").strip()
    if not name:
        return _bad("Enter your name")
    if not raw_email and not raw_phone:
        return _bad("Enter an email or a phone number")
    if len(password) < 6:
        return _bad("Password must be at least 6 characters")
    if not data.get("accept_privacy"):
        return _bad("Please accept the privacy policy")

    email = phone = None
    if raw_email:
        email, err = normalize_email(raw_email)
        if err:
            return _bad(err)
        if AppAccount.objects.filter(email=email).exists():
            return _bad(TAKEN["email"], status=409)
    if raw_phone:
        phone, err = normalize_phone(raw_phone)
        if err:
            return _bad(err)
        if AppAccount.objects.filter(phone=phone).exists():
            return _bad(TAKEN["phone"], status=409)

    account = AppAccount(name=name[:100], email=email, phone=phone, source="self")
    account.set_password(password)
    try:
        account.save()
    except IntegrityError:
        return _bad("This email or phone number is already registered. Sign in instead.", status=409)
    return _issue_session(account, data)


# --------------------------------------------------------------------------- profile
@csrf_exempt
@require_http_methods(["POST"])
@app_token_required
def profile_update(request):
    """Add or change email / phone (current password required) and set or change the username.

    Body: any of {email, phone, username, current_password}. An empty string removes email or phone
    only if the account keeps another way to sign in. Returns the updated user."""
    data = json_body(request)
    if data is None:
        return _bad("Invalid JSON")
    account = request.account
    changes = {}

    for field, normalizer in (("email", normalize_email), ("phone", normalize_phone)):
        if field not in data:
            continue
        raw = (data.get(field) or "").strip()
        if not raw:
            changes[field] = None
            continue
        value, err = normalizer(raw)
        if err:
            return _bad(err)
        if value != getattr(account, field):
            if AppAccount.objects.filter(**{field: value}).exclude(id=account.id).exists():
                return _bad(TAKEN[field], status=409)
            changes[field] = value

    if ("email" in changes or "phone" in changes) and not account.check_password(data.get("current_password") or ""):
        return _bad("Your current password is incorrect", status=403)

    if "username" in data:
        raw = (data.get("username") or "").strip()
        if raw:
            value, err = normalize_username(raw)
            if err:
                return _bad(err)
            if value != account.username:
                if AppAccount.objects.filter(username=value).exclude(id=account.id).exists():
                    return _bad(TAKEN["username"], status=409)
                changes["username"] = value
        else:
            changes["username"] = None

    # Keep at least one way to sign in: an admin/partner account that HAS an email keeps it (the
    # admin or partner manages it); any account keeps an email or a phone.
    new_email = changes.get("email", account.email)
    new_phone = changes.get("phone", account.phone)
    if account.source != "self" and account.email and not new_email:
        return _bad("This account's email can't be removed")
    if not new_email and not new_phone:
        return _bad("Keep an email or a phone number to sign in with")

    if changes:
        for k, v in changes.items():
            setattr(account, k, v)
        try:
            account.save()
        except IntegrityError:
            return _bad("That email, phone or username is already in use", status=409)
    return JsonResponse({"success": True, "user": account_dict(account)})


@require_http_methods(["GET"])
@app_token_required
def username_check(request):
    """Live 'available?' check while the user types a username. ?u=<name>"""
    value, err = normalize_username(request.GET.get("u"))
    if err:
        return JsonResponse({"available": False, "message": err})
    taken = AppAccount.objects.filter(username=value).exclude(id=request.account.id).exists()
    return JsonResponse({
        "available": not taken,
        "username": value,
        "message": TAKEN["username"] if taken else "Available",
    })


# --------------------------------------------------------------------------- install registry
_HELLO_FIELDS = {
    "os_version": 40, "locale": 20, "time_zone": 60, "device_model": 80,
    "webview_version": 40, "install_source": 60,
}


@csrf_exempt
@require_http_methods(["POST"])
def device_hello(request):
    """Every install checks in (launch + FCM token change), signed in or not.

    Body: {device_id, device_secret?, fcm_token?, app_version?, os_version?, locale?, time_zone?,
           device_model?, webview_version?, install_source?, notifications_allowed?, updates_enabled?}
    The first hello issues a device_secret; later hellos for that device_id must send it back, so
    nobody can take over another install's row by guessing its id. With a valid Bearer token the
    signed-in account is attached; without one the account is left as it is (sign-out detaches it
    via devices/unregister). Country comes from Cloudflare's header; the IP itself is never stored."""
    data = json_body(request)
    if data is None:
        return _bad("Invalid JSON")
    device_id = (data.get("device_id") or "").strip()[:255]
    if not device_id:
        return _bad("device_id is required")
    if _rate_limited(f"hello:{_client_ip(request)}", 60, 3600):
        return _bad("Too many requests", status=429)

    account = None
    key = _extract_bearer(request)
    if key:
        token = AppAuthToken.objects.select_related("account").filter(key=key).first()
        if token and token.is_valid and token.account.is_active:
            account = token.account
            if token.device_id != device_id:
                token.device_id = device_id
                token.save(update_fields=["device_id"])

    now = timezone.now()
    with transaction.atomic():
        device = AppDevice.objects.filter(device_id=device_id).first()
        if device is None:
            device = AppDevice(device_id=device_id, installed_at=now, fcm_token="")
        elif device.device_secret and device.device_secret != (data.get("device_secret") or ""):
            return _bad("This device is registered with a different secret", status=403)
        if not device.device_secret:
            device.device_secret = secrets.token_hex(24)

        if data.get("fcm_token"):
            device.fcm_token = str(data["fcm_token"])
        if data.get("app_version"):
            device.app_version = str(data["app_version"])[:50]
        for field, limit in _HELLO_FIELDS.items():
            if field in data:
                setattr(device, field, str(data.get(field) or "")[:limit])
        if "notifications_allowed" in data:
            device.notifications_allowed = bool(data.get("notifications_allowed"))
        if "updates_enabled" in data:
            device.updates_enabled = bool(data.get("updates_enabled"))
        country = (request.META.get("HTTP_CF_IPCOUNTRY") or "").strip().upper()
        if country and country != "XX":
            device.country = country[:4]
        if account is not None:
            device.account = account
            if not device.converted_at:
                device.converted_at = now
        device.platform = device.platform or "android"
        device.last_seen = now
        device.is_active = True
        device.save()
    return JsonResponse({"success": True, "device_secret": device.device_secret})


# --------------------------------------------------------------------------- partners page
def _connection_dict(c):
    return {
        "id": str(c.id),
        "partner": c.partner.name,
        "status": c.status if c.partner_active else "suspended",   # not_enabled | enabled | disabled | suspended
        "added_at": c.created_at.isoformat(),
        "locked_until": c.locked_until.isoformat() if c.is_locked else None,
    }


def _my_connections(account):
    return PartnerConnection.objects.filter(account=account, partner__is_active=True).select_related("partner")


@require_http_methods(["GET"])
@app_token_required
def partners(request):
    """Partners that added this user, with their status."""
    return JsonResponse({"partners": [_connection_dict(c) for c in _my_connections(request.account)]})


@csrf_exempt
@require_http_methods(["POST"])
@app_token_required
def partner_enable(request, connection_id):
    """Enable a partner by entering the password that partner gave the user.
    5 wrong tries → 15-minute cool-down."""
    c = _my_connections(request.account).filter(id=connection_id).first()
    if not c:
        return _bad("Partner not found", status=404)
    if not c.partner_active:
        return _bad(f"{c.partner.name} has paused your access", status=409)
    if c.is_locked:
        mins = max(1, int((c.locked_until - timezone.now()).total_seconds() // 60) + 1)
        return _bad(f"Too many wrong tries. Try again in {mins} min.", status=429)
    data = json_body(request) or {}
    if not c.check_password(data.get("password") or ""):
        c.failed_attempts += 1
        if c.failed_attempts >= PARTNER_MAX_ATTEMPTS:
            c.failed_attempts = 0
            c.locked_until = timezone.now() + timedelta(minutes=PARTNER_LOCK_MINUTES)
            c.save(update_fields=["failed_attempts", "locked_until", "updated_at"])
            return _bad(f"Too many wrong tries. Try again in {PARTNER_LOCK_MINUTES} min.", status=429)
        c.save(update_fields=["failed_attempts", "updated_at"])
        left = PARTNER_MAX_ATTEMPTS - c.failed_attempts
        return _bad(f"Wrong {c.partner.name} password. {left} tr{'y' if left == 1 else 'ies'} left.", status=403)
    c.status = "enabled"
    c.failed_attempts = 0
    c.locked_until = None
    c.enabled_at = timezone.now()
    # The partner password shows it's really them: a blank email / phone on their account now gets
    # the one this partner has for them.
    filled = c.fill_pending()
    c.save(update_fields=["status", "failed_attempts", "locked_until", "enabled_at",
                          "pending_email", "pending_phone", "updated_at"])
    body = {"success": True, "partner": _connection_dict(c)}
    if filled:
        body["filled"] = filled
    return JsonResponse(body)


@csrf_exempt
@require_http_methods(["POST"])
@app_token_required
def partner_disable(request, connection_id):
    """Turn a partner off. Its links disappear and its prompts are refused (the partner is told
    'user disabled <partner>'). Enabling again needs the partner password."""
    c = _my_connections(request.account).filter(id=connection_id).first()
    if not c:
        return _bad("Partner not found", status=404)
    c.status = "disabled"
    c.disabled_at = timezone.now()
    c.save(update_fields=["status", "disabled_at", "updated_at"])
    # Anything the partner had waiting for this user is cancelled.
    c.account.action_requests.filter(partner=c.partner, status="pending").update(status="expired")
    return JsonResponse({"success": True, "partner": _connection_dict(c)})
