"""Page tokens — window.SyncUp.token — only on websites the admin switched on.

A website gets a token only when a SyncUp link or a home shortcut pointing at it has "Give SyncUp
token" on (give_token). The app puts the token on that website's own pages (never on other sites,
never in Incognito); the site's backend pushes with it plus the SyncUp notify key
(POST /app/v1/partner/notify).

  signed in   one token per (account, website) — a push reaches all of that user's phones
  signed out  one token per (phone, website)   — a push reaches that phone only (shortcuts and
              general links; a user's own links need sign-in)

Tokens are random and different for every website, so two sites can't match up users; deleting a
website's rows (reset) makes the app fetch new ones on its next sync. A user's own link counts only
for that user; a general link or a shortcut counts for everyone who sees it.

Tokens from before this (one signed token per account, or per link for app v6) keep working for
LEGACY_DAYS after the release, so sites that saved one don't break overnight.
"""
import secrets
from datetime import timedelta
from urllib.parse import urlsplit

from django.db import IntegrityError
from django.db.models import Q
from django.utils import timezone

from .models import AppConfig, AppLink, AppSiteToken, HomeShortcut

TOKEN_PREFIX = "su_"
LEGACY_DAYS = 60


def site_host(url):
    """The website a URL belongs to: lower-case host without a leading "www." ("" if none)."""
    try:
        host = (urlsplit((url or "").strip()).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _hosts(urls):
    return {h for h in (site_host(u) for u in urls) if h}


def enabled_hosts(account=None):
    """Websites that get a token for this user (account) or, with no account, for a signed-out
    phone: switched-on shortcuts and general links (only for a user who sees general links), plus
    — signed in — the user's own switched-on links (partner links only while that partner is
    enabled, as on the Work home)."""
    from .serializers import live_partner_ids  # serializers imports this module

    urls = list(
        HomeShortcut.objects.filter(give_token=True, is_active=True, category__is_active=True)
        .values_list("url", flat=True)
    )
    if account is None or account.gets_general_links():
        urls += list(
            AppLink.objects.filter(give_token=True, is_active=True, account__isnull=True).values_list("url", flat=True)
        )
    if account is not None:
        urls += list(
            AppLink.objects.filter(give_token=True, is_active=True, account=account)
            .filter(Q(partner__isnull=True) | Q(partner_id__in=live_partner_ids(account)))
            .values_list("url", flat=True)
        )
    return _hosts(urls)


def tokens_for(account=None, device=None, hosts=None):
    """[{"host", "token"}] for every switched-on website, creating tokens the first time.
    Pass account (signed in) or device (signed out)."""
    start_legacy_window()
    owner = {"account": account} if account is not None else {"device": device}
    if owner.get("device") is None and account is None:
        return []
    hosts = enabled_hosts(account) if hosts is None else set(hosts)
    if not hosts:
        return []
    have = dict(AppSiteToken.objects.filter(host__in=hosts, **owner).values_list("host", "token"))
    for host in hosts - have.keys():
        try:
            row, _ = AppSiteToken.objects.get_or_create(host=host, defaults={"token": new_token()}, **owner)
            have[host] = row.token
        except IntegrityError:  # a parallel request created it — use that one
            row = AppSiteToken.objects.filter(host=host, **owner).first()
            if row:
                have[host] = row.token
    return [{"host": h, "token": have[h]} for h in sorted(have)]


def token_for_link(account, link):
    """App v6 puts a link's own token on that link's tab: hand it the per-site token, and only when
    the link is switched on."""
    if not link.give_token or account is None:
        return ""
    host = site_host(link.url)
    if not host:
        return ""
    found = tokens_for(account=account, hosts={host})
    return found[0]["token"] if found else ""


def new_token():
    return TOKEN_PREFIX + secrets.token_urlsafe(30)


def find(token):
    """The AppSiteToken for a page token, or None (old-style tokens never match)."""
    if not isinstance(token, str) or not token.startswith(TOKEN_PREFIX):
        return None
    return AppSiteToken.objects.select_related("account", "device").filter(token=token).first()


def touch(site_token):
    """Note the token was used, at most once a minute."""
    now = timezone.now()
    if site_token.last_used_at is None or (now - site_token.last_used_at).total_seconds() > 60:
        AppSiteToken.objects.filter(pk=site_token.pk).update(last_used_at=now)


def reset_host(host):
    """Forget every token for a website. Apps get fresh ones on their next sync; a site's saved
    tokens stop working at once. Returns how many were removed."""
    deleted, _ = AppSiteToken.objects.filter(host=site_host(f"https://{host}") or host).delete()
    return deleted


def start_legacy_window():
    """The first time the new code serves a request, old tokens get LEGACY_DAYS more days."""
    cfg = AppConfig.load()
    if cfg.legacy_page_tokens_until is None:
        until = timezone.localdate() + timedelta(days=LEGACY_DAYS)
        AppConfig.objects.filter(pk=cfg.pk, legacy_page_tokens_until__isnull=True).update(
            legacy_page_tokens_until=until)
        cfg.legacy_page_tokens_until = until
    return cfg.legacy_page_tokens_until


def legacy_tokens_allowed():
    """Old-style tokens (signed, one per account or per link) still push?"""
    return timezone.localdate() <= start_legacy_window()
