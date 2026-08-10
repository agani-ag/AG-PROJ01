"""Plain dict builders for JSON responses (no DRF)."""
from django.db.models import Q

from .models import AppReminder


def account_dict(account):
    return {
        "id": str(account.id),
        "name": account.name,
        "email": account.email,
    }


def link_dict(link):
    return {
        "id": str(link.id),
        "title": link.title,
        "url": link.url,
        "description": link.description or "",
        "icon": link.icon or "",
    }


def links_for(account):
    """Active links for an account, ordered (matches the app's UrlItem list)."""
    return [link_dict(link) for link in account.links.filter(is_active=True)]


def reminder_dict(reminder):
    # Tap target: a custom URL wins (campaign/form, works for broadcasts); otherwise the
    # account link, if it's still active.
    if reminder.custom_url:
        link_url = reminder.custom_url
        link_title = reminder.title
    else:
        link = reminder.link if (reminder.link and reminder.link.is_active) else None
        link_url = link.url if link else ""
        link_title = link.title if link else ""
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
    }


def reminders_for(account):
    """Active reminders targeted at this account, plus broadcasts (account is null)."""
    qs = (
        AppReminder.objects.filter(is_active=True)
        .filter(Q(account=account) | Q(account__isnull=True))
        .select_related("link")
    )
    return [reminder_dict(r) for r in qs]
