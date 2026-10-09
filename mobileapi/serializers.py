"""Plain dict builders for JSON responses (no DRF)."""
from django.core import signing
from django.db.models import Q

from .models import AppLink, AppReminder, PartnerConnection

# Salt for the notification token the app puts on pages as window.SyncUp.token.
PARTNER_NOTIFY_SALT = "syncup-partner-notify"


def make_account_token(account):
    """The user's notification token. App v7 puts it on every page in every tab; a site pushes to
    this user by sending it with the SyncUp notify key (POST /app/v1/partner/notify)."""
    return signing.dumps({"account_id": account.id}, salt=PARTNER_NOTIFY_SALT)


def make_notify_token(link):
    """The older per-link token: app v6 puts it on that link's own tab only. Still accepted."""
    return signing.dumps({"account_id": link.account_id, "link_id": link.id}, salt=PARTNER_NOTIFY_SALT)


def account_dict(account):
    # `email` is always a string: v5 builds read it as non-null, so a phone-only account sends "".
    return {
        "id": str(account.id),
        "name": account.name,
        "email": account.email or "",
        "phone": account.phone or "",
        "username": account.username or "",
        "source": account.source,
        "notify_token": make_account_token(account),
    }


def link_dict(link):
    return {
        "id": str(link.id),
        "title": link.title,
        "url": link.url,
        "description": link.description or "",
        "icon": link.icon or "",
        # Where the link comes from, for grouping on the app's Work home: a partner (by name) or
        # SyncUp (the admin). Older app versions ignore these.
        "source": "partner" if link.partner_id else "admin",
        "source_name": link.partner.name if link.partner_id else "",
        # For app v6, which puts it on this link's tab (v7 uses the account's token on every page).
        # Per-user, so general (account-less) links never carry one.
        "notify_token": make_notify_token(link) if link.account_id else "",
    }


def live_partner_ids(account):
    """Partners that can currently reach this user: enabled by the user and not suspended."""
    return list(
        PartnerConnection.objects.filter(
            account=account, status="enabled", partner_active=True, partner__is_active=True,
        ).values_list("partner_id", flat=True)
    )


def links_for(account):
    """The app's URL list: this user's own active links, then the shared general links.

    A partner's links appear only while that partner's connection is enabled (and not suspended);
    admin links (no partner) always appear. General links (account is null) are appended for every
    account — no per-account opt-out any more (2026-10-09).
    """
    own = (
        AppLink.objects.filter(is_active=True, account=account)
        .filter(Q(partner__isnull=True) | Q(partner_id__in=live_partner_ids(account)))
        .select_related("partner").order_by("title")
    )
    result = [link_dict(link) for link in own]
    general = AppLink.objects.filter(is_active=True, account__isnull=True).select_related("partner").order_by("title")
    result += [link_dict(link) for link in general]
    return result


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
