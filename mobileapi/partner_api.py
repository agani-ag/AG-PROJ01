"""
Partner provisioning API — mounted at /partner/v1/ (see partner_urls.py).

A partner authenticates with its API key (`Authorization: Bearer <key>`) and manages ONLY its own
users and their links, scoped through PartnerConnection (the partner's access key on a SyncUp
account). A SyncUp account belongs to the person; each partner that adds them gets a connection
with its own partner password:

  * a NEW email → we create the account; the partner password is also its sign-in, and the
    connection is enabled straight away (exactly how partner users worked before);
  * an email that ALREADY has a SyncUp account → a "not enabled" connection; the user enables it in
    the app (Partners page) with the password the partner gave them. Until then the partner's links
    are kept hidden and its notifications/prompts are refused with the reason.

Built for a growing user base:

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
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from . import fcm, telegram
from .identity import normalize_email
from .models import (
    AppAccount, AppActionRequest, AppDevice, AppLink, AppNotificationLog, AppPartner,
    AppTelegramLog, PartnerConnection,
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


def _user_json(c):
    """A partner's view of one user = its connection. `status`: enabled | not_enabled | disabled
    (disabled = the user turned this partner off). `is_active` is the partner's own on/off."""
    a = c.account
    return {
        "id": str(a.id),
        "external_id": c.external_id or "",
        "name": a.name,
        "email": a.email or "",
        "is_active": c.partner_active and a.is_active,
        "status": c.status,
    }


def _link_json(link):
    """Link for partner responses — the app-facing dict plus the partner's own key (external_id),
    so a partner can confirm/match a link by key rather than by URL."""
    data = link_dict(link)
    data["external_id"] = link.external_id or ""
    return data


def _user_with_links(c):
    """User + this partner's links for them — returned from create/upsert."""
    links = c.account.links.filter(partner=c.partner)
    return {"user": _user_json(c), "links": [_link_json(link) for link in links]}


def _not_reachable(c):
    """Why this partner can't reach the user right now (None = it can). Refusals say why."""
    if not c.partner_active or not c.account.is_active:
        return "User is deactivated"
    if c.status == "not_enabled":
        return f"User hasn't enabled {c.partner.name} yet"
    if c.status == "disabled":
        return f"User disabled {c.partner.name}"
    return None


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
    return (
        PartnerConnection.objects.select_related("account", "partner")
        .filter(partner=partner, account_id=user_id).first()
    )


def _by_external(partner, external_id):
    return (
        PartnerConnection.objects.select_related("account", "partner")
        .filter(partner=partner, external_id=external_id).first()
    )


def _owns_signin(c):
    """True when this partner created the account and it still signs in with the partner password —
    then the partner's password resets and on/off also apply to the SyncUp sign-in (as before)."""
    a = c.account
    return a.partner_id == c.partner_id and a.partner_signin


def _apply_user_fields(c, data):
    """Apply name / is_active / password / external_id from a payload to a connection (and, for a
    partner-first account the partner still controls, to the account). Returns an error or None."""
    a = c.account
    owns = _owns_signin(c)
    if data.get("name") and owns:
        a.name = data["name"].strip()
    if "is_active" in data:
        c.partner_active = bool(data.get("is_active"))
        if owns and not a.partner_connections.exclude(id=c.id).exists():
            a.is_active = c.partner_active  # the only partner of an account it still controls
        if not c.partner_active:
            _cancel_pending(c)
    if "external_id" in data:
        c.external_id = (data.get("external_id") or "").strip() or None
    if data.get("password"):
        if len(data["password"]) < 6:
            return "password must be at least 6 characters"
        c.set_password(data["password"])
        if owns:
            a.set_password(data["password"])  # partner-first: reset also resets the sign-in
    return None


def _save(c):
    c.account.save()
    c.save()


def _cancel_pending(c):
    """The partner stepped away from this user: anything it had waiting is cancelled."""
    AppActionRequest.objects.filter(partner=c.partner, account=c.account, status="pending").update(status="expired")


def _new_connection_push(c):
    """Tell an existing SyncUp user (on v6+) that a partner added them."""
    tokens = [
        d.fcm_token for d in AppDevice.objects.filter(account=c.account, is_active=True).exclude(fcm_token="")
        if d.app_version_code >= 6
    ]
    if tokens:
        fcm.push(
            tokens, f"{c.partner.name} added you",
            f"Open SyncUp → Partners and enter the password {c.partner.name} gave you to turn it on.",
            source="partner_api", account=c.account, partner=c.partner, data={"type": "partners"},
        )


def _create_user(partner, data, existing_keys=None):
    """Add a user for the partner. Returns (JsonResponse, connection | None).

    A new email creates the SyncUp account (partner password = sign-in, enabled at once). An email
    that already has an account gets a "not enabled" connection the user turns on in the app. A user
    this partner already has → 409. `existing_keys` (bulk) is the partner's external_ids, kept
    current so an in-batch duplicate is caught."""
    name = (data.get("name") or "").strip()
    email, email_err = normalize_email(data.get("email"))
    password = data.get("password") or ""
    external_id = (data.get("external_id") or "").strip() or None
    if not name or not data.get("email"):
        return _err("name and email are required"), None
    if email_err:
        return _err(email_err), None
    if len(password) < 6:
        return _err("password must be at least 6 characters"), None
    if external_id:
        key_taken = external_id in existing_keys if existing_keys is not None \
            else PartnerConnection.objects.filter(partner=partner, external_id=external_id).exists()
        if key_taken:
            return _err("A user with this external_id already exists", 409), None

    account = AppAccount.objects.filter(email=email).first()
    if account and PartnerConnection.objects.filter(partner=partner, account=account).exists():
        return _err("A user with this email already exists", 409), None

    try:
        if account is None:
            # Partner users are single-purpose (their partner's links only), so the shared "general
            # links" are OFF by default — the SyncUp admin can turn them on per user.
            account = AppAccount(
                name=name, email=email, partner=partner, source="partner", partner_signin=True,
                show_general_links=False,
            )
            account.set_password(password)
            account.save()
            c = PartnerConnection(partner=partner, account=account, external_id=external_id,
                                  password=account.password, status="enabled", enabled_at=timezone.now())
            c.save()
        else:
            c = PartnerConnection(partner=partner, account=account, external_id=external_id)
            c.set_password(password)
            c.save()
            _new_connection_push(c)
    except IntegrityError:
        return _err("A user with this email or external_id already exists", 409), None
    if existing_keys is not None and external_id:
        existing_keys.add(external_id)
    _apply_links(account, partner, data.get("links"))
    return None, c


def _created_response(c):
    body = {"success": True, "created": True, **_user_with_links(c)}
    if c.status == "not_enabled":
        body["message"] = (
            f"This person already has a SyncUp account. They need to open SyncUp → Partners and enter "
            f"the password you set to turn {c.partner.name} on."
        )
    return JsonResponse(body, status=201)


def _apply_links(account, partner, links):
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
        try:
            if key:
                AppLink.objects.update_or_create(
                    account=account, external_id=key, defaults={**fields, "partner": partner},
                )
            else:
                AppLink.objects.create(account=account, partner=partner, **fields)
        except IntegrityError:
            continue  # the key is already used by another partner's link on this account


# --------------------------------------------------------------------------- users: collection
@partner_api_required
@require_http_methods(["GET", "POST"])
def users(request):
    if request.method == "POST":
        data = _json(request)
        if data is None:
            return _err("Invalid JSON")
        err, c = _create_user(request.partner, data)
        if err:
            return err
        return _created_response(c)

    # GET — cursor-paginated, optionally filtered by email or external_id.
    qs = PartnerConnection.objects.filter(partner=request.partner).select_related("account", "partner")
    email = (request.GET.get("email") or "").strip().lower()
    ext = (request.GET.get("external_id") or "").strip()
    if email:
        qs = qs.filter(account__email=email)
    if ext:
        qs = qs.filter(external_id=ext)
    try:
        limit = max(1, min(MAX_PAGE, int(request.GET.get("limit", DEFAULT_PAGE))))
    except (TypeError, ValueError):
        limit = DEFAULT_PAGE
    cursor = request.GET.get("cursor")
    if cursor:
        try:
            qs = qs.filter(account_id__gt=int(cursor))
        except (TypeError, ValueError):
            return _err("Invalid cursor")
    rows = list(qs.order_by("account_id")[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    return JsonResponse({
        "success": True,
        "users": [_user_json(c) for c in rows],
        "next_cursor": str(rows[-1].account_id) if has_more else None,
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

    # Pre-fetch this batch's external_ids in one query; _create_user keeps the set current.
    keys = [(it.get("external_id") or "").strip() for it in items if isinstance(it, dict)]
    existing_keys = set(
        PartnerConnection.objects.filter(
            partner=request.partner, external_id__in=[k for k in keys if k],
        ).values_list("external_id", flat=True)
    )

    results = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            results.append({"index": i, "status": "error", "message": "not an object"})
            continue
        err, c = _create_user(request.partner, item, existing_keys)
        if err:
            body = json.loads(err.content)
            results.append({"index": i, "status": "error", "message": body.get("message")})
        else:
            results.append({"index": i, "status": "created", "user": _user_json(c)})
    created = sum(1 for r in results if r["status"] == "created")
    return JsonResponse({"success": True, "created": created, "results": results})


# --------------------------------------------------------------------------- users: single
def _user_detail(request, c):
    """Shared GET/PATCH/DELETE handler once the connection is resolved (id or external_id).
    DELETE switches this partner off for the user (reversible with PATCH is_active=true); the
    SyncUp account itself stays — except an account this partner still fully controls, which is
    deactivated as before."""
    if request.method == "GET":
        return JsonResponse({"success": True, "user": _user_json(c)})
    if request.method == "DELETE":
        _apply_user_fields(c, {"is_active": False})
        _save(c)
        return JsonResponse({"success": True, "message": "User deactivated"})
    data = _json(request)  # PATCH
    if data is None:
        return _err("Invalid JSON")
    err = _apply_user_fields(c, data)
    if err:
        return _err(err)
    try:
        _save(c)
    except IntegrityError:
        return _err("external_id already in use", 409)
    return JsonResponse({"success": True, "user": _user_json(c)})


@partner_api_required
@require_http_methods(["GET", "PATCH", "DELETE"])
def user_by_id(request, user_id):
    c = _by_id(request.partner, user_id)
    if not c:
        return _err("User not found", 404)
    return _user_detail(request, c)


@partner_api_required
@require_http_methods(["GET", "PATCH", "DELETE", "PUT"])
def user_by_external(request, external_id):
    c = _by_external(request.partner, external_id)
    if request.method == "PUT":
        # Upsert: create if missing, else update — idempotent for nightly sync.
        data = _json(request)
        if data is None:
            return _err("Invalid JSON")
        if c:
            err = _apply_user_fields(c, {**data, "external_id": external_id})
            if err:
                return _err(err)
            new_email = (data.get("email") or "").strip().lower()
            if new_email and new_email != c.account.email:
                # Only an account this partner still fully controls can have its email changed.
                if not _owns_signin(c):
                    return _err("This user's email is managed by the user, not the partner", 409)
                if AppAccount.objects.filter(email=new_email).exclude(id=c.account_id).exists():
                    return _err("A user with this email already exists", 409)
                c.account.email = new_email
            try:
                _save(c)
            except IntegrityError:
                return _err("Conflict saving user", 409)
            _apply_links(c.account, request.partner, data.get("links"))  # replace-by-key link upsert
            return JsonResponse({"success": True, "created": False, **_user_with_links(c)})
        err, c = _create_user(request.partner, {**data, "external_id": external_id})
        if err:
            return err
        return _created_response(c)

    if not c:
        return _err("User not found", 404)
    return _user_detail(request, c)


# --------------------------------------------------------------------------- links
def _links_collection(request, c):
    account = c.account
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
        # Links for a user who hasn't enabled this partner yet are kept, hidden until they do.
        try:
            if key:
                link, _ = AppLink.objects.update_or_create(
                    account=account, external_id=key, defaults={**fields, "partner": c.partner},
                )
            else:
                link = AppLink.objects.create(account=account, partner=c.partner, **fields)
        except IntegrityError:
            return _err("external_id already used by another link for this user", 409)
        return JsonResponse({"success": True, "link": _link_json(link)}, status=201)
    return JsonResponse(
        {"success": True, "links": [_link_json(link) for link in account.links.filter(partner=c.partner)]}
    )


@partner_api_required
@require_http_methods(["GET", "POST"])
def links_by_user_id(request, user_id):
    c = _by_id(request.partner, user_id)
    if not c:
        return _err("User not found", 404)
    return _links_collection(request, c)


@partner_api_required
@require_http_methods(["GET", "POST"])
def links_by_user_external(request, external_id):
    c = _by_external(request.partner, external_id)
    if not c:
        return _err("User not found", 404)
    return _links_collection(request, c)


@partner_api_required
@require_http_methods(["PATCH", "DELETE"])
def link_detail(request, link_id):
    link = (
        AppLink.objects.filter(id=link_id, partner=request.partner)
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


def _push(tokens, title, body, url, *, account=None, partner=None, save_log=True):
    """Send one push to a set of device tokens and log it. Returns the fcm.PushResult."""
    data = {"link_url": url, "link_title": ""} if url else None
    return fcm.push(tokens, title[:100], body[:200], source="partner_api", account=account,
                    partner=partner, data=data, save_log=save_log)


@partner_api_required
@require_http_methods(["POST"])
def notify_user_by_id(request, user_id):
    c = _by_id(request.partner, user_id)
    if not c:
        return _err("User not found", 404)
    if _not_reachable(c):
        return _err(_not_reachable(c), 409)
    account = c.account
    parsed, err = _notify_payload(request)
    if err:
        return err
    title, body, url = parsed
    delivered = _push(_tokens_for_accounts({"account": account}), title, body, url,
                      account=account, partner=request.partner).delivered
    return JsonResponse({"success": True, "delivered": delivered})


@partner_api_required
@require_http_methods(["POST"])
def notify_user_by_external(request, external_id):
    c = _by_external(request.partner, external_id)
    if not c:
        return _err("User not found", 404)
    if _not_reachable(c):
        return _err(_not_reachable(c), 409)
    account = c.account
    parsed, err = _notify_payload(request)
    if err:
        return err
    title, body, url = parsed
    delivered = _push(_tokens_for_accounts({"account": account}), title, body, url,
                      account=account, partner=request.partner).delivered
    return JsonResponse({"success": True, "delivered": delivered})


@partner_api_required
@require_http_methods(["POST"])
def notify_all(request):
    """Broadcast a push to ALL of the partner's active users' devices (one fan-out send)."""
    parsed, err = _notify_payload(request)
    if err:
        return err
    title, body, url = parsed
    tokens = _tokens_for_accounts({
        "account__is_active": True,
        "account__partner_connections__partner": request.partner,
        "account__partner_connections__status": "enabled",
        "account__partner_connections__partner_active": True,
    })
    delivered = _push(tokens, title, body, url, partner=request.partner).delivered
    return JsonResponse({"success": True, "delivered": delivered})


@partner_api_required
@require_http_methods(["POST"])
def notify_bulk(request):
    """Send many DISTINCT push messages in ONE request — e.g. 60 overdue reminders, each to a
    different user with its own text — instead of 60 separate calls (one request stays under the
    per-minute rate limit). Each item targets a user by `external_id` or `user_id`. Returns a
    per-item result so the partner sees exactly which landed and which didn't."""
    data = _json(request)
    if data is None:
        return _err("Invalid JSON")
    items = data.get("messages")
    if not isinstance(items, list) or not items:
        return _err("messages must be a non-empty list")
    if len(items) > MAX_BULK:
        return _err(f"messages can hold at most {MAX_BULK} items")

    # Resolve every target account and its active tokens up front — two queries, not per-item.
    ext_ids = {str(it["external_id"]).strip() for it in items
               if isinstance(it, dict) and it.get("external_id")}
    int_ids = {int(it["user_id"]) for it in items
               if isinstance(it, dict) and str(it.get("user_id") or "").isdigit()}
    by_ext, by_id = {}, {}
    for c in PartnerConnection.objects.select_related("account", "partner").filter(partner=request.partner).filter(
        Q(external_id__in=ext_ids) | Q(account_id__in=int_ids)
    ):
        by_id[c.account_id] = c
        if c.external_id:
            by_ext[c.external_id] = c
    tokens_by_acc = {}
    for acc_id, tok in (
        AppDevice.objects.filter(is_active=True, account_id__in=list(by_id))
        .exclude(fcm_token="").exclude(fcm_token__isnull=True)
        .values_list("account_id", "fcm_token")
    ):
        tokens_by_acc.setdefault(acc_id, []).append(tok)

    # Validate each item in order; collect the ones ready to send.
    results = [None] * len(items)
    ready = []  # (index, account, title, body, url)
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            results[i] = {"error": "each message must be an object"}
            continue
        ref, conn = {}, None
        if it.get("external_id"):
            ref["external_id"] = str(it["external_id"]).strip()
            conn = by_ext.get(ref["external_id"])
        elif str(it.get("user_id") or "").isdigit():
            ref["user_id"] = int(it["user_id"])
            conn = by_id.get(ref["user_id"])
        acc = conn.account if conn else None
        title = (it.get("title") or "").strip()
        body = (it.get("body") or "").strip()
        url = (it.get("url") or "").strip()
        if not conn:
            results[i] = {**ref, "error": "User not found"}
        elif _not_reachable(conn):
            results[i] = {**ref, "error": _not_reachable(conn)}
        elif not title:
            results[i] = {**ref, "error": "title is required"}
        elif url and not url.lower().startswith("https://"):
            results[i] = {**ref, "error": "url must be https://"}
        else:
            ready.append((i, ref, acc, title, body, url))

    # Fan the sends out concurrently and JOIN before responding (safe on PythonAnywhere — the
    # threads finish inside the request, unlike a fire-and-forget background thread).
    def _one(job):
        _i, _ref, _acc, _title, _body, _url = job
        # The log row comes back unsaved and is bulk-created below, off the worker threads.
        return _i, _ref, _push(tokens_by_acc.get(_acc.id, []), _title, _body, _url,
                               account=_acc, partner=request.partner, save_log=False)

    logs, sent = [], 0
    if ready:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(16, len(ready))) as pool:
            for i, ref, result in pool.map(_one, ready):
                results[i] = {**ref, "delivered": result.delivered}
                sent += 1
                logs.append(result.log)
    if logs:
        AppNotificationLog.objects.bulk_create(logs)
    return JsonResponse({"success": True, "sent": sent, "count": len(items), "results": results})


# --------------------------------------------------------------------------- Telegram relay
def _telegram_one(partner, chat_id, text, parse_mode):
    """Send one Telegram message + log it. Returns a per-item result dict."""
    ok, info = telegram.send(chat_id, text, parse_mode=parse_mode)
    AppTelegramLog.objects.create(
        partner=partner, chat_id=str(chat_id)[:64], text=text or "",
        status="sent" if ok else "failed",
        message_id=info if ok else "", error="" if ok else info,
    )
    return {"chat_id": str(chat_id), "message_id": info} if ok else {"chat_id": str(chat_id), "error": info}


@partner_api_required
@require_http_methods(["POST"])
def telegram_send(request):
    """Deliver one report/message to a Telegram chat our bot can reach (the partner adds our bot to
    their group / the person starts our bot, then sends us the chat_id)."""
    if not telegram.is_configured():
        return _err("Telegram is not configured on the server", 503)
    data = _json(request)
    if data is None:
        return _err("Invalid JSON")
    chat_id = str(data.get("chat_id") or "").strip()
    text = (data.get("text") or "").strip()
    if not chat_id:
        return _err("chat_id is required")
    if not text:
        return _err("text is required")
    result = _telegram_one(request.partner, chat_id, text, data.get("parse_mode"))
    if "error" in result:
        return JsonResponse({"success": False, **result}, status=502)
    return JsonResponse({"success": True, **result})


@partner_api_required
@require_http_methods(["POST"])
def telegram_bulk(request):
    """Deliver many Telegram reports/messages in ONE request (each to its own chat_id) — same
    batching win as notify/bulk. Returns a per-item result."""
    if not telegram.is_configured():
        return _err("Telegram is not configured on the server", 503)
    data = _json(request)
    if data is None:
        return _err("Invalid JSON")
    items = data.get("messages")
    if not isinstance(items, list) or not items:
        return _err("messages must be a non-empty list")
    if len(items) > MAX_BULK:
        return _err(f"messages can hold at most {MAX_BULK} items")

    results = [None] * len(items)
    ready = []  # (index, chat_id, text, parse_mode)
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            results[i] = {"error": "each message must be an object"}
            continue
        chat_id = str(it.get("chat_id") or "").strip()
        text = (it.get("text") or "").strip()
        if not chat_id:
            results[i] = {"error": "chat_id is required"}
        elif not text:
            results[i] = {"chat_id": chat_id, "error": "text is required"}
        else:
            ready.append((i, chat_id, text, it.get("parse_mode")))

    # Threads do ONLY the Telegram HTTP send (no DB writes — concurrent SQLite writes deadlock).
    # Results come back to the main thread, which writes all the logs in one bulk_create.
    sent, logs = 0, []
    if ready:
        from concurrent.futures import ThreadPoolExecutor
        def _job(job):
            i, chat_id, text, pm = job
            ok, info = telegram.send(chat_id, text, parse_mode=pm)
            return i, chat_id, text, ok, info
        with ThreadPoolExecutor(max_workers=min(8, len(ready))) as pool:
            for i, chat_id, text, ok, info in pool.map(_job, ready):
                results[i] = {"chat_id": chat_id, "message_id": info} if ok else {"chat_id": chat_id, "error": info}
                sent += 1 if ok else 0
                logs.append(AppTelegramLog(
                    partner=request.partner, chat_id=chat_id[:64], text=text,
                    status="sent" if ok else "failed",
                    message_id=info if ok else "", error="" if ok else info,
                ))
    if logs:
        AppTelegramLog.objects.bulk_create(logs)
    return JsonResponse({"success": True, "sent": sent, "count": len(items), "results": results})


# --------------------------------------------------------------------------- actions (verify)
_DEFAULT_MSG = {
    "otp": "Your verification code",
    "code": "Enter your verification code",
    "number": "Approve your sign-in",
    "notice": "You have a message to read",
    "approve": "Approve or reject this request",
}


def _create_action(partner, account, data):
    """Build an AppActionRequest + push a priority prompt. Returns (JsonResponse, action|None)."""
    atype = (data.get("type") or "").strip().lower()
    if atype not in ("otp", "code", "number", "notice", "approve"):
        return _err("type must be one of: otp, code, number, notice, approve"), None
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
    elif atype == "approve":
        # A yes/no decision — two buttons. Labels are optional (default Approve / Reject).
        params = {
            "approve_label": (data.get("approve_label") or "Approve").strip()[:24],
            "reject_label": (data.get("reject_label") or "Reject").strip()[:24],
        }
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
    # High-importance "Verification" channel → heads-up banner even when backgrounded/killed.
    android = fcm.build_android_config({
        "priority": "high", "notification_priority": "max", "channel_id": "syncup_verify",
    })
    payload = {"type": "action", "action_id": str(action.id), "action_type": atype}
    delivered = fcm.push(
        tokens, action.title[:100], notif_body[:200], source="verification",
        account=action.account, partner=action.partner, data=payload, android=android,
        log_body="Code: (hidden)" if atype == "otp" else None,   # never store a one-time code
    ).delivered
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
    c = _by_id(request.partner, user_id)
    if not c:
        return _err("User not found", 404)
    if _not_reachable(c):
        return _err(_not_reachable(c), 409)
    data = _json(request)
    if data is None:
        return _err("Invalid JSON")
    err, action = _create_action(request.partner, c.account, data)
    return err or _action_response(action)


@partner_api_required
@require_http_methods(["POST"])
def action_by_external(request, external_id):
    c = _by_external(request.partner, external_id)
    if not c:
        return _err("User not found", 404)
    if _not_reachable(c):
        return _err(_not_reachable(c), 409)
    data = _json(request)
    if data is None:
        return _err("Invalid JSON")
    err, action = _create_action(request.partner, c.account, data)
    return err or _action_response(action)


def _external_id_for(a):
    c = PartnerConnection.objects.filter(partner_id=a.partner_id, account_id=a.account_id).first()
    return (c.external_id or "") if c else ""


def _action_status_dict(a):
    """Full, pollable state of a prompt — the same result the callback carries, but fetchable any
    time (the callback is best-effort / fire-once, so this is the partner's safety net)."""
    resp = a.response or {}
    out = {
        "request_id": str(a.id),
        "type": a.action_type,
        "status": a.status,  # pending | completed | expired
        "value": resp.get("value"),
        "user": {"id": str(a.account_id), "external_id": _external_id_for(a)},
        "delivered": a.delivered,
        "created_at": a.created_at.isoformat(),
        "expires_at": a.expires_at.isoformat(),
        "responded_at": a.completed_at.isoformat() if a.completed_at else None,
    }
    if a.status == "completed":
        if a.action_type == "approve":
            out["approved"] = resp.get("value") == "approved"
        elif a.action_type == "notice":
            out["acknowledged"] = True
    return out


@partner_api_required
@require_http_methods(["GET"])
def action_status(request, request_id):
    """Look up a prompt's answer after the fact — e.g. the partner was down when the user tapped
    Approve, so the one-shot callback was lost. Scoped to the partner's own actions."""
    a = (
        AppActionRequest.objects.select_related("account")
        .filter(id=request_id, partner=request.partner).first()
    )
    if not a:
        return _err("Action not found", 404)
    if a.status == "pending" and a.is_expired:
        a.status = "expired"
        a.save(update_fields=["status"])
    return JsonResponse({"success": True, **_action_status_dict(a)})
