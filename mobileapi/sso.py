"""Sign in with SyncUp — for SyncHub's tools (Hibiscus, Pocket Money, J6).

On the websites of general links with "Sign in with SyncUp" on (allow_sso) the app puts
window.SyncUp = {v: 1, account: {signedIn, id}, signIn()} — never the person's name or email.

  1. signIn() shows the app's own sheet — asked once per person for all of SyncHub (consent is kept
     here, so a new phone with the same account isn't asked again; Disconnect withdraws it).
  2. The app asks POST /app/v1/sso/code for a one-time code: one person, one website, 60 seconds.
  3. The page hands the code to its own server, which swaps it at POST /app/v1/hub/sso/check (with
     the sign-in key) for the person's id, name, email and phone. Every check uses the code up.

The id ("su_…") is random, made once per person for SyncHub, and never changes — not on sign-out,
reinstall, a new phone or a page-token reset — so SyncHub can key its accounts on it. SyncUp never
sees anything the person does in SyncHub; the admin sees counts only.
"""
import hashlib
import hmac
import re
import secrets
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .auth import app_token_required, json_body
from .models import AppConfig, AppLink, AppSsoCode, AppSsoIdentity
from .site_tokens import site_host
from .views import _client_ip, _rate_limited

CLIENT = "synchub"
CLIENT_NAME = "SyncHub"
ID_PREFIX = "su_"
CODE_SECONDS = 60
KEEP_CODES_DAYS = 31          # used codes are kept a month for the admin's counts, then deleted
SHARES = ["name", "email", "phone"]


def _error(text, reason, status=400):
    return JsonResponse({"success": False, "error": text, "reason": reason}, status=status)


def _host(value):
    """A bare host as SyncHub or the app sends it: lower-case, no port, no leading "www."."""
    if not isinstance(value, str):
        return ""
    value = value.strip().lower()
    if "//" in value:
        return site_host(value)
    value = value.split("/")[0].split(":")[0]
    return value[4:] if value.startswith("www.") else value


def sso_links():
    """The general links whose websites may use Sign in with SyncUp."""
    return AppLink.objects.filter(account__isnull=True, is_active=True, allow_sso=True)


def sso_hosts():
    return sorted({h for h in (site_host(u) for u in sso_links().values_list("url", flat=True)) if h})


def identity_for(account):
    """This person's SyncHub identity, made the first time (the id never changes after that)."""
    found = AppSsoIdentity.objects.filter(account=account, client=CLIENT).first()
    if found:
        return found
    try:
        with transaction.atomic():
            return AppSsoIdentity.objects.create(
                account=account, client=CLIENT, sub=ID_PREFIX + secrets.token_urlsafe(24)[:32],
            )
    except IntegrityError:  # a parallel request made it
        return AppSsoIdentity.objects.get(account=account, client=CLIENT)


def app_info(account=None):
    """What the app needs to put window.SyncUp on pages: the websites, and — signed in — the id and
    whether the person already said yes (then a page can sign them in quietly)."""
    hosts = sso_hosts()
    if not hosts:
        return {"hosts": [], "id": None, "connected": False}
    if account is None:
        return {"hosts": hosts, "id": None, "connected": False}
    ident = identity_for(account)
    return {"hosts": hosts, "id": ident.sub, "connected": ident.connected}


def _phone(value):
    digits = re.sub(r"\D", "", value or "")
    return digits[-10:] if digits else ""


def _hash(code):
    return hashlib.sha256(code.encode()).hexdigest()


# --------------------------------------------------------------------------- the app's side
@csrf_exempt
@require_http_methods(["POST"])
@app_token_required
def sso_code(request):
    """A one-time sign-in code for the page that asked. Body: {host, consent?}.
    Without consent given before (or now, consent: true) the answer is needs_consent and no code."""
    if _rate_limited(f"sso_code:{request.account.pk}", 30, 600):
        return _error("Too many sign-ins — try again in a few minutes", "slow_down", 429)
    data = json_body(request)
    if not isinstance(data, dict):
        return _error("Invalid JSON", "bad_request")
    host = _host(data.get("host"))
    if not host or host not in sso_hosts():
        return _error("This website can't use Sign in with SyncUp", "not_allowed", 403)
    ident = identity_for(request.account)
    if not ident.connected:
        if data.get("consent") is not True:
            return JsonResponse({"success": True, "needs_consent": True, "client": CLIENT_NAME, "shares": SHARES})
        ident.consented_at, ident.disconnected_at = timezone.now(), None
        ident.save(update_fields=["consented_at", "disconnected_at"])
    code = secrets.token_urlsafe(32)
    AppSsoCode.objects.create(code_hash=_hash(code), identity=ident, host=host)
    AppSsoCode.objects.filter(created_at__lt=timezone.now() - timedelta(days=KEEP_CODES_DAYS)).delete()
    return JsonResponse({"success": True, "code": code, "expires_in": CODE_SECONDS})


@require_http_methods(["GET"])
@app_token_required
def sso_sites(request):
    """You → Signed-in sites: where SyncUp signs this person in, since when, and what it shares."""
    ident = AppSsoIdentity.objects.filter(account=request.account, client=CLIENT).first()
    if ident is None or not ident.connected:
        return JsonResponse({"success": True, "sites": []})
    tools = list(sso_links().order_by("title").values_list("title", flat=True))
    return JsonResponse({"success": True, "sites": [{
        "client": CLIENT, "name": CLIENT_NAME, "tools": tools,
        "since": ident.consented_at.isoformat(), "shares": SHARES,
    }]})


@csrf_exempt
@require_http_methods(["POST"])
@app_token_required
def sso_disconnect(request):
    """Stop signing this person in to SyncHub. Their SyncHub account and its data stay there."""
    ident = AppSsoIdentity.objects.filter(account=request.account, client=CLIENT).first()
    if ident is not None and ident.connected:
        ident.consented_at, ident.disconnected_at = None, timezone.now()
        ident.save(update_fields=["consented_at", "disconnected_at"])
    return JsonResponse({"success": True})


# --------------------------------------------------------------------------- SyncHub's side
@csrf_exempt
@require_http_methods(["POST"])
def hub_sso_check(request):
    """SyncHub's server swaps a code for who the person is. Body: {key, code, host}."""
    if _rate_limited(f"hub_sso:{_client_ip(request)}", 120, 3600):
        return _error("Too many calls — try again later", "slow_down", 429)
    data = json_body(request)
    if not isinstance(data, dict):
        return _error("Invalid JSON", "bad_request")
    expected = AppConfig.load().sso_key or ""
    if not expected:
        return _error("Sign in with SyncUp is off (no sign-in key made in SyncUp's admin)", "off", 503)
    key = data.get("key")
    if not isinstance(key, str) or not hmac.compare_digest(key.encode(), expected.encode()):
        return _error("A valid key is required", "bad_key", 403)
    code = data.get("code")
    if not isinstance(code, str) or not code:
        return _error("Unknown or used code", "bad_code")
    with transaction.atomic():
        row = (AppSsoCode.objects.select_for_update().select_related("identity__account")
               .filter(code_hash=_hash(code)).first())
        if row is None or row.used_at is not None:
            return _error("Unknown or used code", "bad_code")
        now = timezone.now()
        row.used_at = now                      # any check uses the code up
        row.save(update_fields=["used_at"])
        if now - row.created_at > timedelta(seconds=CODE_SECONDS):
            return _error("The code is more than a minute old", "expired")
        if _host(data.get("host")) != row.host:
            return _error("The code was made for another website", "wrong_host")
        ident = row.identity
        account = ident.account
        if not account.is_active:
            return _error("That SyncUp account no longer exists", "account_gone")
        if not ident.connected:
            return _error("The person disconnected SyncHub in SyncUp", "disconnected", 403)
        row.ok = True
        row.save(update_fields=["ok"])
        ident.last_sign_in_at = now
        ident.save(update_fields=["last_sign_in_at"])
    return JsonResponse({"success": True, "user": {
        "id": ident.sub, "name": account.name or "", "email": account.email or "", "phone": _phone(account.phone),
    }})


def admin_counts():
    """Admin → General links: sign-ins in the last 30 days, the people they were, disconnects."""
    since = timezone.now() - timedelta(days=30)
    ok = AppSsoCode.objects.filter(ok=True, used_at__gte=since)
    return {
        "sign_ins": ok.count(),
        "people": ok.values("identity").distinct().count(),
        "disconnected": AppSsoIdentity.objects.filter(client=CLIENT, disconnected_at__isnull=False).count(),
    }
