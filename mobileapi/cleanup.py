"""Shared data-retention logic — run by both `manage.py cleanup` and the /cron/cleanup endpoint.

Prunes rows that accumulate forever (logs, expired tokens/sessions, old chat, dead devices,
finished cron reminders) using the admin-editable windows on AppConfig. Bulk deletes only, so a
run is a handful of fast SQL statements. Never touches active/current data:

  * unread chat is always kept, regardless of age;
  * device (delivery="device") reminders and any active/pending reminder are never removed;
  * only deactivated devices are eligible.

Returns a stats dict of {table: rows_deleted} so callers can report what happened.
"""
from datetime import timedelta

from django.contrib.sessions.models import Session
from django.db import connection
from django.db.models import Q
from django.utils import timezone

from .models import (
    AppAuthToken,
    AppChatMessage,
    AppConfig,
    AppDevice,
    AppNotificationLog,
    AppReminder,
)


def run_cleanup(vacuum=False):
    cfg = AppConfig.load()
    now = timezone.now()
    stats = {}

    # Always: expired auth tokens and expired sessions (nulls = non-expiring, left alone).
    stats["expired_tokens"] = AppAuthToken.objects.filter(expires_at__lt=now).delete()[0]
    stats["expired_sessions"] = Session.objects.filter(expire_date__lt=now).delete()[0]

    if cfg.cleanup_log_days:
        cutoff = now - timedelta(days=cfg.cleanup_log_days)
        stats["notification_logs"] = AppNotificationLog.objects.filter(sent_at__lt=cutoff).delete()[0]

    if cfg.cleanup_chat_days:
        cutoff = now - timedelta(days=cfg.cleanup_chat_days)
        # "Unread by the recipient" — a user message the admin hasn't read, or an admin message
        # the user hasn't read — is kept no matter how old.
        unread = Q(sender="user", read_by_admin=False) | Q(sender="admin", read_by_user=False)
        stats["chat_messages"] = (
            AppChatMessage.objects.filter(created_at__lt=cutoff).exclude(unread).delete()[0]
        )

    if cfg.cleanup_inactive_device_days:
        cutoff = now - timedelta(days=cfg.cleanup_inactive_device_days)
        stats["inactive_devices"] = (
            AppDevice.objects.filter(is_active=False, last_seen__lt=cutoff).delete()[0]
        )

    if cfg.cleanup_done_reminder_days:
        cutoff = now - timedelta(days=cfg.cleanup_done_reminder_days)
        stats["finished_reminders"] = (
            AppReminder.objects.filter(
                delivery="cron", status__in=["sent", "expired", "failed"], updated_at__lt=cutoff,
            ).delete()[0]
        )

    if vacuum:
        # Reclaims file space freed by the deletes. Locks the DB briefly, so it's opt-in and best
        # run off-peak. Must run outside a transaction (autocommit) — fine from the command and
        # from the cron endpoint (ATOMIC_REQUESTS is off).
        with connection.cursor() as cursor:
            cursor.execute("VACUUM;")
        stats["vacuumed"] = True

    return stats
