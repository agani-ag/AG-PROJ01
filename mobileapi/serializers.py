"""Plain dict builders for JSON responses (no DRF)."""
from django.db.models import Q

from .models import AppLink, AppReminder, PartnerConnection

# Salt of the old-style page tokens (one per account, or per link for app v6). New tokens are per
# website and random (site_tokens.py); old ones are still accepted for a while after the release.
PARTNER_NOTIFY_SALT = "syncup-partner-notify"


def account_dict(account):
    # `email` is always a string: v5 builds read it as non-null, so a phone-only account sends "".
    return {
        "id": str(account.id),
        "name": account.name,
        "email": account.email or "",
        "phone": account.phone or "",
        "username": account.username or "",
        "source": account.source,
    }


def link_dict(link, notify_token=""):
    return {
        "id": str(link.id),
        "title": link.title,
        "url": link.url,
        "description": link.description or "",
        "icon": link.icon or "",
        # The picked icon's colour (blank = blue); with no icon the app shows the website's own icon.
        "color": link.color or "",
        # Where the link comes from, for grouping on the app's Work home: a partner (by name) or
        # SyncUp (the admin). Older app versions ignore these.
        "source": "partner" if link.partner_id else "admin",
        "source_name": link.partner.name if link.partner_id else "",
        # A general link (no account): every phone shows it, signed in or not, in its own
        # "For everyone" group. Older app versions ignore it.
        "general": link.account_id is None,
        # App v6 puts this on the link's own tab: the website's page token, only when the link has
        # "Give SyncUp token" on (newer apps get the list in /sync as token_sites instead).
        "notify_token": notify_token,
        "give_token": link.give_token,
    }


def live_partner_ids(account):
    """Partners that can currently reach this user: enabled by the user and not suspended."""
    return list(
        PartnerConnection.objects.filter(
            account=account, status="enabled", partner_active=True, partner__is_active=True,
        ).values_list("partner_id", flat=True)
    )


def active_general_links():
    """The admin's general links (no account) — for everyone, signed in or not."""
    return AppLink.objects.filter(is_active=True, account__isnull=True).select_related("partner").order_by("title")


def links_for(account):
    """The app's URL list: this user's own active links, then the shared general links.

    A partner's links appear only while that partner's connection is enabled (and not suspended);
    admin links (no partner) always appear. General links (account is null) are appended unless
    this is a partner user, or the admin switched them off for this user (gets_general_links).
    """
    from .site_tokens import site_host, tokens_for

    own = list(
        AppLink.objects.filter(is_active=True, account=account)
        .filter(Q(partner__isnull=True) | Q(partner_id__in=live_partner_ids(account)))
        .select_related("partner").order_by("title")
    )
    general = list(active_general_links()) if account.gets_general_links() else []
    tokens = {}
    if any(link.give_token for link in own + general):
        tokens = {t["host"]: t["token"] for t in tokens_for(account=account)}

    def _dict(link):
        return link_dict(link, tokens.get(site_host(link.url), "") if link.give_token else "")

    return [_dict(link) for link in own] + [_dict(link) for link in general]


def reminder_tap_target(reminder):
    """(url, title) opened when the notification is tapped.

    A custom URL wins (campaign/form, works for broadcasts); otherwise the account link, if it's
    still active. Shared by the device payload and the server-side cron push so both open the
    same place.
    """
    if reminder.custom_url:
        return reminder.custom_url, reminder.title
    link = reminder.link if (reminder.link and reminder.link.is_active) else None
    return (link.url, link.title) if link else ("", "")


def reminder_dict(reminder):
    link_url, link_title = reminder_tap_target(reminder)
    return {
        "id": str(reminder.id),
        "title": reminder.title,
        "body": reminder.body,
        "link_url": link_url,
        "link_title": link_title,
        "image_url": reminder.image_url or "",
        # ISO-8601 instant (human-readable) + epoch millis (what the app schedules against).
        "scheduled_at": reminder.scheduled_at.isoformat(),
        "scheduled_at_ms": int(reminder.scheduled_at.timestamp() * 1000),
        "recurrence": reminder.recurrence,
        # For "Repeat N times": gap between fires (ms) and total fire count (0 = unlimited).
        # once/daily leave these at 0 — the app schedules those from `recurrence`.
        "repeat_interval_ms": (
            reminder.repeat_interval_seconds * 1000 if reminder.recurrence == "interval" else 0
        ),
        "repeat_count": reminder.repeat_count if reminder.recurrence == "interval" else 0,
    }


def reminders_for(account):
    """Active DEVICE-fired reminders for this account, plus broadcasts (account is null).

    delivery="cron" rows are excluded on purpose: the server pushes those itself, so syncing them
    would make the phone set a local alarm as well and the user would get the notification twice.
    Filtering here (rather than in the app) means every already-shipped APK is covered — old
    clients simply receive fewer rows.
    """
    qs = (
        AppReminder.objects.filter(is_active=True, delivery="device")
        .filter(Q(account=account) | Q(account__isnull=True))
        .select_related("link")
    )
    return [reminder_dict(r) for r in qs]


def chat_message_dict(m):
    return {
        "id": m.id,
        "sender": m.sender,  # "user" | "admin"
        "body": m.body,
        "created_at": m.created_at.isoformat(),
        "created_at_ms": int(m.created_at.timestamp() * 1000),
    }
