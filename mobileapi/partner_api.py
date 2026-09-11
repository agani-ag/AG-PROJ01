"""
Partner provisioning API — mounted at /partner/v1/ (see partner_urls.py).

A partner authenticates with its API key (`Authorization: Bearer <key>`) and manages ONLY its own
users and their links, scoped through AppAccount.partner. Built for a growing user base:

  * users can be addressed by our internal id OR the partner's own `external_id`,
  * `PUT /users/external/<id>` upserts (idempotent nightly sync, no 409 churn),
  * list endpoints are cursor-paginated and filterable,
  * a bulk-create endpoint provisions many users in one call.

Separate from /app/v1/ (mobile auth) and /mobile/ (admin): its own key gate, CSRF-exempt,
rate-limited per partner.
"""
import json
from datetime import timedelta
from functools import wraps

from django.core.cache import cache
from django.db import IntegrityError
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from . import fcm
from .models import (
    AppAccount, AppActionRequest, AppDevice, AppLink, AppNotificationLog, AppPartner,
)
from .serializers import link_dict

MAX_PAGE = 200
DEFAULT_PAGE = 50
MAX_BULK = 200


# --------------------------------------------------------------------------- helpers
def _json(request):
    try:
        return json.loads(request.body or b"{}")
    except (ValueError, TypeError):
        return None


def _err(msg, status=400):
    return JsonResponse({"success": False, "message": msg}, status=status)


def _https(url):
    url = (url or "").strip()
    return url if url.lower().startswith("https://") else None


def _user_json(a):
    return {
        "id": str(a.id),
        "external_id": a.external_id or "",
        "name": a.name,
        "email": a.email,
        "is_active": a.is_active,
    }


def _link_json(link):
    """Link for partner responses — the app-facing dict plus the partner's own key (external_id),
    so a partner can confirm/match a link by key rather than by URL."""
    data = link_dict(link)
    data["external_id"] = link.external_id or ""
    return data


def _user_with_links(a):
    """User + its links — returned from create/upsert so a login can be issued in one call."""
    return {"user": _user_json(a), "links": [_link_json(link) for link in a.links.all()]}


def partner_api_required(view):
    """Gate on the partner API key; sets request.partner. Rate-limited per partner, CSRF-exempt."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        header = request.META.get("HTTP_AUTHORIZATION", "")
        raw_key = header[7:].strip() if header.startswith("Bearer ") else ""
        if not raw_key:
            return _err("Authentication required", 401)
        partner = AppPartner.objects.filter(
            api_key_hash=AppPartner.hash_key(raw_key), is_active=True,
        ).first()
        if not partner:
            return _err("Invalid API key", 401)

        rk = f"partner_api:{partner.id}"
        count = cache.get(rk, 0)
        if count >= partner.rate_limit_per_min:
            return _err("Rate limit exceeded, try again shortly", 429)
        cache.set(rk, count + 1, 60)

        now = timezone.now()
        if partner.last_used_at is None or (now - partner.last_used_at).total_seconds() > 60:
            partner.last_used_at = now
            partner.save(update_fields=["last_used_at"])

        request.partner = partner
        return view(request, *args, **kwargs)

    return csrf_exempt(wrapper)


def _by_id(partner, user_id):
    return AppAccount.objects.filter(id=user_id, partner=partner).first()


def _by_external(partner, external_id):
    return AppAccount.objects.filter(partner=partner, external_id=external_id).first()


def _apply_user_fields(account, data):
    """Set name/is_active/password/external_id from a payload. Returns an error string or None."""
    if data.get("name"):
        account.name = data["name"].strip()
    if "is_active" in data:
        account.is_active = bool(data.get("is_active"))
    if "external_id" in data:
        account.external_id = (data.get("external_id") or "").strip() or None
    if data.get("password"):
        if len(data["password"]) < 6:
            return "password must be at least 6 characters"
        account.set_password(data["password"])
    return None


def _create_user(partner, data, existing_emails=None, existing_keys=None):
    """Create a new account for the partner. Returns (JsonResponse, account | None).

    For bulk, pass pre-fetched `existing_emails` / `existing_keys` sets so uniqueness is checked
    in memory (no per-user query); successful creates are added back so intra-batch dups are caught."""
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    external_id = (data.get("external_id") or "").strip() or None
    if not name or not email:
        return _err("name and email are required"), None
    if len(password) < 6:
        return _err("password must be at least 6 characters"), None
    email_taken = email in existing_emails if existing_emails is not None \
        else AppAccount.objects.filter(email=email).exists()
    if email_taken:
        return _err("A user with this email already exists", 409), None
    if external_id:
        key_taken = external_id in existing_keys if existing_keys is not None \
            else bool(_by_external(partner, external_id))
        if key_taken:
            return _err("A user with this external_id already exists", 409), None
    # Partner users are single-purpose (their partner's link[s] only), so the shared "general links"
    # are OFF by default — the SyncUp admin can turn them on per user from the Edit Account page.
    account = AppAccount(
        name=name, email=email, partner=partner, external_id=external_id,
        show_general_links=False,
    )
    account.set_password(password)
    try:
        account.save()
    except IntegrityError:
        return _err("A user with this email or external_id already exists", 409), None
    # Keep the in-memory sets current so a later duplicate in the same batch is caught.
    if existing_emails is not None:
        existing_emails.add(email)
    if existing_keys is not None and external_id:
        existing_keys.add(external_id)
    _apply_links(account, data.get("links"))
    return None, account


def _apply_links(account, links):
    """Replace-by-key upsert of a user's links, so provisioning a login (user + link) is one call.
    Each item: {external_id (key), title, url [https], description?, icon?}. A matching key updates
    the existing link; no key creates a new one. Invalid entries are skipped."""
    if not isinstance(links, list):
        return
    for item in links:
        if not isinstance(item, dict):
            continue
        title = (item.get("title") or "").strip()
        url = _https(item.get("url"))
        if not title or not url:
            continue
        fields = {
            "title": title, "url": url,
            "description": (item.get("description") or "").strip() or None,
            "icon": (item.get("icon") or "").strip() or None,
        }
        key = (item.get("external_id") or "").strip() or None
        if key:
            AppLink.objects.update_or_create(account=account, external_id=key, defaults=fields)
        else:
            AppLink.objects.create(account=account, **fields)


# --------------------------------------------------------------------------- users: collection
@partner_api_required
@require_http_methods(["GET", "POST"])
def users(request):
    if request.method == "POST":
        data = _json(request)
        if data is None:
            return _err("Invalid JSON")
        err, account = _create_user(request.partner, data)
        if err:
            return err
        return JsonResponse({"success": True, **_user_with_links(account)}, status=201)

    # GET — cursor-paginated, optionally filtered by email or external_id.
    qs = AppAccount.objects.filter(partner=request.partner)
    email = (request.GET.get("email") or "").strip().lower()
    ext = (request.GET.get("external_id") or "").strip()
    if email:
        qs = qs.filter(email=email)
    if ext:
        qs = qs.filter(external_id=ext)
    try:
        limit = max(1, min(MAX_PAGE, int(request.GET.get("limit", DEFAULT_PAGE))))
    except (TypeError, ValueError):
        limit = DEFAULT_PAGE
    cursor = request.GET.get("cursor")
    if cursor:
        try:
            qs = qs.filter(id__gt=int(cursor))
        except (TypeError, ValueError):
            return _err("Invalid cursor")
    rows = list(qs.order_by("id")[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    return JsonResponse({
        "success": True,
        "users": [_user_json(a) for a in rows],
        "next_cursor": str(rows[-1].id) if has_more else None,
    })


@partner_api_required
@require_http_methods(["POST"])
def users_bulk(request):
    """Create many users in one call. Body: {"users": [ {...}, ... ]} (max 200). Each item is
    independent — the response reports per-item created / error, so a partial batch still succeeds."""
    data = _json(request)
    if data is None or not isinstance(data.get("users"), list):
        return _err("Body must be {\"users\": [ ... ]}")
    items = data["users"]
    if not items:
        return _err("users list is empty")
    if len(items) > MAX_BULK:
        return _err(f"Too many users in one call (max {MAX_BULK})")

    # Pre-fetch which emails / external_ids in this batch already exist — two queries total instead
    # of two per user. _create_user then checks in memory and keeps the sets current.
    emails = [(it.get("email") or "").strip().lower() for it in items if isinstance(it, dict)]
    keys = [(it.get("external_id") or "").strip() for it in items if isinstance(it, dict)]
    existing_emails = set(
        AppAccount.objects.filter(email__in=[e for e in emails if e]).values_list("email", flat=True)
    )
    existing_keys = set(
        AppAccount.objects.filter(
            partner=request.partner, external_id__in=[k for k in keys if k],
        ).values_list("external_id", flat=True)
    )

    results = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            results.append({"index": i, "status": "error", "message": "not an object"})
            continue
        err, account = _create_user(request.partner, item, existing_emails, existing_keys)
        if err:
            body = json.loads(err.content)
            results.append({"index": i, "status": "error", "message": body.get("message")})
        else:
            results.append({"index": i, "status": "created", "user": _user_json(account)})
    created = sum(1 for r in results if r["status"] == "created")
    return JsonResponse({"success": True, "created": created, "results": results})


# --------------------------------------------------------------------------- users: single
def _user_detail(request, account):
    """Shared GET/PATCH/DELETE handler once the account is resolved (id or external_id)."""
    if request.method == "GET":
        return JsonResponse({"success": True, "user": _user_json(account)})
    if request.method == "DELETE":
        account.is_active = False
        account.save(update_fields=["is_active"])
        return JsonResponse({"success": True, "message": "User deactivated"})
    data = _json(request)  # PATCH
    if data is None:
        return _err("Invalid JSON")
    err = _apply_user_fields(account, data)
    if err:
        return _err(err)
    try:
        account.save()
    except IntegrityError:
        return _err("external_id already in use", 409)
    return JsonResponse({"success": True, "user": _user_json(account)})


@partner_api_required
@require_http_methods(["GET", "PATCH", "DELETE"])
def user_by_id(request, user_id):
    account = _by_id(request.partner, user_id)
    if not account:
        return _err("User not found", 404)
    return _user_detail(request, account)


@partner_api_required
@require_http_methods(["GET", "PATCH", "DELETE", "PUT"])
def user_by_external(request, external_id):
    account = _by_external(request.partner, external_id)
    if request.method == "PUT":
        # Upsert: create if missing, else update — idempotent for nightly sync.
        data = _json(request)
        if data is None:
            return _err("Invalid JSON")
        if account:
            err = _apply_user_fields(account, {**data, "external_id": external_id})
            if err:
                return _err(err)
            new_email = (data.get("email") or "").strip().lower()
            if new_email and new_email != account.email:
                if AppAccount.objects.filter(email=new_email).exclude(id=account.id).exists():
                    return _err("A user with this email already exists", 409)
                account.email = new_email
            try:
                account.save()
            except IntegrityError:
                return _err("Conflict saving user", 409)
            _apply_links(account, data.get("links"))  # replace-by-key link upsert
            return JsonResponse({"success": True, "created": False, **_user_with_links(account)})
        err, account = _create_user(request.partner, {**data, "external_id": external_id})
        if err:
            return err
        return JsonResponse({"success": True, "created": True, **_user_with_links(account)}, status=201)

    if not account:
        return _err("User not found", 404)
    return _user_detail(request, account)


# --------------------------------------------------------------------------- links
def _links_collection(request, account):
    if request.method == "POST":
        data = _json(request)
        if data is None:
            return _err("Invalid JSON")
        title = (data.get("title") or "").strip()
        url = _https(data.get("url"))
        if not title or not url:
            return _err("title and a valid https:// url are required")
        key = (data.get("external_id") or "").strip() or None
        fields = {
            "title": title, "url": url,
            "description": (data.get("description") or "").strip() or None,
            "icon": (data.get("icon") or "").strip() or None,
        }
        # If a key is given, upsert by it (replace-by-key) so a repeat add doesn't duplicate.
        if key:
            link, _ = AppLink.objects.update_or_create(
                account=account, external_id=key, defaults=fields,
            )
        else:
            link = AppLink.objects.create(account=account, **fields)
        return JsonResponse({"success": True, "link": _link_json(link)}, status=201)
    return JsonResponse(
        {"success": True, "links": [_link_json(link) for link in account.links.all()]}
    )


@partner_api_required
@require_http_methods(["GET", "POST"])
def links_by_user_id(request, user_id):
    account = _by_id(request.partner, user_id)
    if not account:
        return _err("User not found", 404)
    return _links_collection(request, account)


@partner_api_required
@require_http_methods(["GET", "POST"])
def links_by_user_external(request, external_id):
    account = _by_external(request.partner, external_id)
    if not account:
        return _err("User not found", 404)
    return _links_collection(request, account)


@partner_api_required
@require_http_methods(["PATCH", "DELETE"])
def link_detail(request, link_id):
    link = (
        AppLink.objects.filter(id=link_id, account__partner=request.partner)
        .select_related("account").first()
    )
    if not link:
        return _err("Link not found", 404)
    if request.method == "DELETE":
        link.delete()
        return JsonResponse({"success": True, "message": "Link deleted"})
    data = _json(request)
    if data is None:
        return _err("Invalid JSON")
    if data.get("title"):
        link.title = data["title"].strip()
    if "url" in data:
        url = _https(data.get("url"))
        if not url:
            return _err("url must be https://")
        link.url = url
    if "description" in data:
        link.description = (data.get("description") or "").strip() or None
    if "icon" in data:
        link.icon = (data.get("icon") or "").strip() or None
    if "is_active" in data:
        link.is_active = bool(data.get("is_active"))
    if "external_id" in data:
        link.external_id = (data.get("external_id") or "").strip() or None
    try:
        link.save()
    except IntegrityError:
        return _err("external_id already in use for this user", 409)
    return JsonResponse({"success": True, "link": _link_json(link)})


# --------------------------------------------------------------------------- notifications
def _notify_payload(request):
    """Parse a notify body → (title, body, url) or (None, error_response)."""
    data = _json(request)
    if data is None:
        return None, _err("Invalid JSON")
    title = (data.get("title") or "").strip()
    if not title:
        return None, _err("title is required")
    body = (data.get("body") or "").strip()
    url = (data.get("url") or "").strip()
    if url and not url.lower().startswith("https://"):
        return None, _err("url must be https:// (opened in the app on tap)")
    return (title, body, url), None


def _tokens_for_accounts(account_filter):
    return list(
        AppDevice.objects.filter(is_active=True, **account_filter)
        .exclude(fcm_token="").exclude(fcm_token__isnull=True)
        .values_list("fcm_token", flat=True)
    )


def _push(tokens, title, body, url):
    """Send one push to a set of device tokens. Returns delivered count."""
    if not tokens or not fcm.is_configured():
        return 0
    data = {"link_url": url, "link_title": ""} if url else None
    try:
        ok, _ = fcm.send(tokens, title[:100], body[:200], data=data)
        return ok
    except Exception:  # noqa: BLE001 — never let a push failure 500 the API
        return 0


@partner_api_required
@require_http_methods(["POST"])
def notify_user_by_id(request, user_id):
    account = _by_id(request.partner, user_id)
    if not account or not account.is_active:
        return _err("User not found", 404)
    parsed, err = _notify_payload(request)
    if err:
        return err
    title, body, url = parsed
    delivered = _push(_tokens_for_accounts({"account": account}), title, body, url)
    AppNotificationLog.objects.create(
        account=account, title=title, body=body,
        data={"link_url": url} if url else None, success_count=delivered, fail_count=0,
    )
    return JsonResponse({"success": True, "delivered": delivered})


@partner_api_required
@require_http_methods(["POST"])
def notify_user_by_external(request, external_id):
    account = _by_external(request.partner, external_id)
    if not account or not account.is_active:
        return _err("User not found", 404)
    parsed, err = _notify_payload(request)
    if err:
        return err
    title, body, url = parsed
    delivered = _push(_tokens_for_accounts({"account": account}), title, body, url)
    AppNotificationLog.objects.create(
        account=account, title=title, body=body,
        data={"link_url": url} if url else None, success_count=delivered, fail_count=0,
    )
    return JsonResponse({"success": True, "delivered": delivered})


@partner_api_required
@require_http_methods(["POST"])
def notify_all(request):
    """Broadcast a push to ALL of the partner's active users' devices (one fan-out send)."""
    parsed, err = _notify_payload(request)
    if err:
        return err
    title, body, url = parsed
    tokens = _tokens_for_accounts(
        {"account__partner": request.partner, "account__is_active": True}
    )
    delivered = _push(tokens, title, body, url)
    AppNotificationLog.objects.create(
        account=None, title=title, body=body,
        data={"link_url": url} if url else None, success_count=delivered, fail_count=0,
    )
    return JsonResponse({"success": True, "delivered": delivered})


# --------------------------------------------------------------------------- actions (verify)
_DEFAULT_MSG = {
    "otp": "Your verification code",
    "code": "Enter your verification code",
    "number": "Approve your sign-in",
    "notice": "You have a message to read",
}


def _create_action(partner, account, data):
    """Build an AppActionRequest + push a priority prompt. Returns (JsonResponse, action|None)."""
    atype = (data.get("type") or "").strip().lower()
    if atype not in ("otp", "code", "number", "notice"):
        return _err("type must be one of: otp, code, number, notice"), None
    callback_url = (data.get("callback_url") or "").strip()
    if not callback_url.lower().startswith("https://"):
        return _err("callback_url (https://) is required"), None
    title = (data.get("title") or "").strip() or "Verification"
    message = (data.get("message") or "").strip()

    params = {}
    if atype == "otp":
        code = str(data.get("code") or "").strip()
        if not code:
            return _err("code is required for type=otp"), None
        params = {"code": code}
    elif atype == "code":
        try:
            length = int(data.get("length") or 6)
        except (TypeError, ValueError):
            length = 6
        params = {"length": max(3, min(10, length))}
    elif atype == "notice":
        # Info-to-acknowledge: the user must scroll the full body, then tap "I Acknowledge".
        body = (data.get("body") or "").strip()
        if not body:
            return _err("body is required for type=notice"), None
        cta_url = (data.get("cta_url") or "").strip()
        if cta_url and not cta_url.lower().startswith("https://"):
            return _err("cta_url must be an https:// URL"), None
        params = {"body": body}
        if cta_url:
            params["cta_url"] = cta_url
            params["cta_label"] = (data.get("cta_label") or "View details").strip()
    else:  # number
        numbers = data.get("numbers")
        if not isinstance(numbers, list) or not (2 <= len(numbers) <= 6):
            return _err("numbers must be a list of 2-6 values for type=number"), None
        params = {"numbers": [str(n) for n in numbers]}

    try:
        ttl = int(data.get("ttl_seconds") or 300)
    except (TypeError, ValueError):
        ttl = 300
    ttl = max(30, min(3600, ttl))

    action = AppActionRequest.objects.create(
        partner=partner, account=account, action_type=atype, title=title,
        message=message, params=params, callback_url=callback_url,
        expires_at=timezone.now() + timedelta(seconds=ttl),
    )
    send_action_push(action)
    return None, action


def send_action_push(action):
    """Deliver a priority push for an action prompt. Sets/returns the device count. OTP shows the
    code right in the notification (it's meant to be read). Shared by the API and the Test console."""
    atype = action.action_type
    notif_body = (
        f"Code: {action.params.get('code', '')}" if atype == "otp"
        else (action.message or _DEFAULT_MSG.get(atype, "Verification needed"))
    )
    tokens = _tokens_for_accounts({"account": action.account})
    delivered = 0
    if tokens and fcm.is_configured():
        # High-importance "Verification" channel → heads-up banner even when backgrounded/killed.
        android = fcm.build_android_config({
            "priority": "high", "notification_priority": "max", "channel_id": "syncup_verify",
        })
        payload = {"type": "action", "action_id": str(action.id), "action_type": atype}
        try:
            delivered, _ = fcm.send(
                tokens, action.title[:100], notif_body[:200], data=payload, android=android,
            )
        except Exception:  # noqa: BLE001
            delivered = 0
    action.delivered = delivered
    action.save(update_fields=["delivered"])
    return delivered


def _action_response(action):
    return JsonResponse({
        "success": True,
        "request_id": str(action.id),
        "delivered": action.delivered,
        "expires_at": action.expires_at.isoformat(),
    })


@partner_api_required
@require_http_methods(["POST"])
def action_by_id(request, user_id):
    account = _by_id(request.partner, user_id)
    if not account or not account.is_active:
        return _err("User not found", 404)
    data = _json(request)
    if data is None:
        return _err("Invalid JSON")
    err, action = _create_action(request.partner, account, data)
    return err or _action_response(action)


@partner_api_required
@require_http_methods(["POST"])
def action_by_external(request, external_id):
    account = _by_external(request.partner, external_id)
    if not account or not account.is_active:
        return _err("User not found", 404)
    data = _json(request)
    if data is None:
        return _err("Invalid JSON")
    err, action = _create_action(request.partner, account, data)
    return err or _action_response(action)
