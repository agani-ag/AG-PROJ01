"""SyncUp's people for SyncHub (AG-PROJ03) — a server-to-server API, one way only: SyncHub asks, SyncUp
answers, and SyncUp never calls SyncHub.

SyncHub copies SyncUp's people into its own accounts ahead of time (its 15-minute cron), so inside the
app its "Use token" button signs a SyncUp person straight in, and their SyncUp password works on
SyncHub's website too. The only guard is the SyncUp links key SyncHub already holds for its General links
(hub_links.py), and here it goes in the body only, never the address, because it can read password hashes:

  POST /app/v1/hub/people      {"key", "version"?}
       → {"success", "version", "host", "token_on", "people": [person, …]}
         or {"success", "unchanged": true, "version"} when nothing changed since `version`
  POST /app/v1/hub/people/who  {"key", "token"}
       → {"success", "person": person}                      a signed-in SyncUp person on the list
         {"success", "person": null, "reason": "phone"}     a signed-out phone: never a person
         {"success", "person": null, "reason": "not_included"}
         404 "Unknown token"                                 not a token for SyncHub's website
  person = {"id", "name", "email", "phone", "password", "token"}

Who is copied: every active account that sees General links, the app's own rule (everyone except partner
users, plus partner users the admin gave General links). The list is complete every time, so someone
missing from it has left. A person's "token" is the page token the app puts on SyncHub's pages for them
(site_tokens), so SyncHub can recognise them and push to them; blank while SyncHub's links don't give
the token.
"""
import hashlib
import json
from collections import Counter

from django.db import IntegrityError, transaction
from django.db.models import Count
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import site_tokens
from .auth import json_body
from .hub_links import _error, _key_error, made_here
from .models import AppAccount, AppSiteToken
from .views import _client_ip, _rate_limited


def synchub_host():
    """SyncHub's website as SyncUp knows it: the host of the links SyncHub added ("" before any)."""
    hosts = Counter(h for h in (site_tokens.site_host(u) for u in made_here().values_list("url", flat=True)) if h)
    return min(hosts, key=lambda h: (-hosts[h], h)) if hosts else ""


def copied_accounts():
    """The people SyncHub gets: active accounts that see General links, oldest first."""
    accounts = AppAccount.objects.filter(is_active=True).annotate(partner_count=Count("partner_connections")).order_by("id")
    return [a for a in accounts if a.gets_general_links(a.partner_count)]


def site_tokens_for(accounts, host):
    """{account id: SyncHub page token}, making the ones that don't exist yet. They are the same rows
    the app is given in /sync, so the page and SyncHub always agree."""
    def have():
        return dict(AppSiteToken.objects.filter(host=host, account__isnull=False).values_list("account_id", "token"))

    found = have()
    missing = [a for a in accounts if a.id not in found]
    if not missing:
        return found
    try:
        with transaction.atomic():
            AppSiteToken.objects.bulk_create(
                [AppSiteToken(host=host, account=a, token=site_tokens.new_token()) for a in missing], batch_size=500)
    except IntegrityError:  # the app's sync made some at the same moment: make the rest one by one
        for a in missing:
            site_tokens.tokens_for(account=a, hosts={host})
    return have()


def person_json(account, token):
    return {
        "id": str(account.id),
        "name": account.name,
        "email": account.email or "",
        "phone": account.phone or "",
        "password": account.password,
        "token": token,
    }


def _start(request, name, limit):
    """Rate limit, JSON body and key: (data, None) or (None, error response)."""
    if _rate_limited(f"{name}:{_client_ip(request)}", limit, 3600):
        return None, _error("Too many calls — try again later", 429)
    data = json_body(request)
    if not isinstance(data, dict):
        return None, _error("Invalid JSON")
    bad = _key_error(data.get("key"))
    return (None, bad) if bad else (data, None)


@csrf_exempt
@require_POST
def people(request):
    data, bad = _start(request, "hub_people", 120)
    if bad:
        return bad
    host = synchub_host()
    token_on = bool(host) and host in site_tokens.enabled_hosts()
    accounts = copied_accounts()
    tokens = site_tokens_for(accounts, host) if token_on else {}
    body = {"host": host, "token_on": token_on, "people": [person_json(a, tokens.get(a.id, "")) for a in accounts]}
    version = hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    if data.get("version") == version:
        return JsonResponse({"success": True, "unchanged": True, "version": version})
    return JsonResponse({"success": True, "version": version, **body})


@csrf_exempt
@require_POST
def who(request):
    data, bad = _start(request, "hub_people_who", 1200)
    if bad:
        return bad
    host = synchub_host()
    site_token = site_tokens.find(data.get("token"))
    if not host or site_token is None or site_token.host != host:
        return _error("Unknown token", 404)
    site_tokens.touch(site_token)
    if site_token.account_id is None:
        return JsonResponse({"success": True, "person": None, "reason": "phone"})
    account = site_token.account
    if not account.is_active or not account.gets_general_links():
        return JsonResponse({"success": True, "person": None, "reason": "not_included"})
    return JsonResponse({"success": True, "person": person_json(account, site_token.token)})
