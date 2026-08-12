"""Plain dict builders for JSON responses (no DRF)."""
from django.core import signing
from django.db.models import Q

from .models import AppReminder

# Salt for the per-link "partner notify" token injected as window.SyncUp.token.
PARTNER_NOTIFY_SALT = "syncup-partner-notify"


def make_notify_token(link):
    return signing.dumps({"account_id": link.account_id, "link_id": link.id}, salt=PARTNER_NOTIFY_SALT)


def account_dict(account):
    return {
        "id": str(account.id),
        "name": account.name,
        "email": account.email,
        "can_manage_links": account.can_manage_links,
    }


def link_dict(link):
    return {
        "id": str(link.id),
        "title": link.title,
        "url": link.url,
        "description": link.description or "",
        "icon": link.icon or "",
        # The app shows a remove (✕) only on links the user added themselves.
        "can_remove": link.created_by_user,
        # Non-empty only when the link opts in — the app injects it as window.SyncUp.token.
        "notify_token": make_notify_token(link) if link.notify_token_enabled else "",
    }


def links_for(account):
    """Active links for an account, ordered (matches the app's UrlItem list)."""
    return [link_dict(link) for link in account.links.filter(is_active=True)]


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
