"""Shared data-retention logic — run by both `manage.py cleanup` and the /cron/cleanup endpoint.

Prunes rows that accumulate forever (logs, expired tokens/sessions, old chat, dead devices,
finished cron reminders) using the admin-editable windows on AppConfig. Bulk deletes only, so a
run is a handful of fast SQL statements. Never touches active/current data:

  * unread chat is always kept, regardless of age;
  * device (delivery="device") reminders and any active/pending reminder are never removed;
  * only deactivated devices are eligible.

Synced items (browser sync) are tidied without phones noticing — nothing is sent to them, and an
account's own data written by a phone that's gone is kept (it's the account's, not the phone's):

  * history: repeat visits of a page older than HISTORY_REPEATS_AFTER_DAYS go, the newest visit of
    each page always stays. Every visit of the last 30 days stays: the app's Most used sites (7 and
    30 days, the TV row) counts them, and a newly signed-in phone builds it from these rows. Phones
    that already have the older visits keep them;
  * open-tab rows of phones that haven't synced in OTHER_DEVICE_DAYS (already hidden from the
    other phones), that are no longer registered, or that are now signed in to someone else;
  * rows of kinds the app no longer syncs (never sent to a phone).

Deleted markers are kept, however old: a phone away when something was deleted would otherwise
never hear of it, keep its own copy, and bring the item back to every phone if it later edits it.

Returns a stats dict of {table: rows_deleted} so callers can report what happened.
"""
from datetime import timedelta

from django.contrib.sessions.models import Session
from django.db import connection
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from .browser_sync import CLIENT_KINDS, OTHER_DEVICE_DAYS
from .models import (
    AppActionRequest,
    AppAuthToken,
    AppChatMessage,
    AppConfig,
    AppDevice,
    AppNotificationLog,
    AppReminder,
    AppTelegramLog,
    SyncItem,
)


STALE_PUBLIC_INSTALL_DAYS = 180
HISTORY_REPEATS_AFTER_DAYS = 30
DELETE_BATCH = 500  # ids per DELETE, under SQLite's limit on query variables


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
        # The Telegram send log and the Verify test / partner prompts keep the same days as the push log.
        stats["telegram_logs"] = AppTelegramLog.objects.filter(created_at__lt=cutoff).delete()[0]
        stats["verify_prompts"] = AppActionRequest.objects.filter(created_at__lt=cutoff).delete()[0]

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

    # Always: signed-out installs nobody has opened in 6 months (they'd only get broadcasts). Installs
    # with an account are never removed here; FCM "unregistered" tokens are switched off on send.
    stats["stale_public_installs"] = AppDevice.objects.filter(
        account__isnull=True, last_seen__lt=now - timedelta(days=STALE_PUBLIC_INSTALL_DAYS),
    ).delete()[0]

    if cfg.cleanup_done_reminder_days:
        cutoff = now - timedelta(days=cfg.cleanup_done_reminder_days)
        stats["finished_reminders"] = (
            AppReminder.objects.filter(
                delivery="cron", status__in=["sent", "expired", "failed"], updated_at__lt=cutoff,
            ).delete()[0]
        )

    stats.update(tidy_sync_items(now))

    if vacuum:
        # Reclaims file space freed by the deletes. Locks the DB briefly, so it's opt-in and best
        # run off-peak. Must run outside a transaction (autocommit) — fine from the command and
        # from the cron endpoint (ATOMIC_REQUESTS is off).
        with connection.cursor() as cursor:
            cursor.execute("VACUUM;")
        stats["vacuumed"] = True

    return stats


def tidy_sync_items(now):
    """The synced-items part of the cleanup (see the module docstring). Returns its stats."""
    stats = {"sync_history_repeats": _delete_ids(_history_repeats(now))}

    owned = AppDevice.objects.filter(device_id=OuterRef("key"), account_id=OuterRef("account_id"))
    stale_tabs = (SyncItem.objects.filter(kind="tabs").annotate(owned=Exists(owned))
                  .filter(Q(changed_at__lt=now - timedelta(days=OTHER_DEVICE_DAYS)) | Q(owned=False)))
    stats["sync_stale_tabs"] = _delete_ids(list(stale_tabs.values_list("pk", flat=True)))

    stats["sync_retired_kinds"] = SyncItem.objects.exclude(kind__in=CLIENT_KINDS | {"tabs"}).delete()[0]
    return stats


def _history_repeats(now):
    """Ids of history visits older than HISTORY_REPEATS_AFTER_DAYS whose page has a newer visit in the
    same account."""
    cutoff_ms = int((now - timedelta(days=HISTORY_REPEATS_AFTER_DAYS)).timestamp() * 1000)
    visits = []
    newest = {}  # (account, url) → the newest visit, (visited_ms, pk)
    rows = SyncItem.objects.filter(kind="history", deleted=False).values_list("pk", "account_id", "updated_ms", "data")
    for pk, account_id, updated_ms, data in rows.iterator():
        data = data if isinstance(data, dict) else {}
        url = data.get("url")
        if not url:
            continue
        visit, page = (data.get("visited_ms") or updated_ms or 0, pk), (account_id, url)
        visits.append((visit, page))
        newest[page] = max(newest.get(page, visit), visit)
    return [visit[1] for visit, page in visits if visit[0] < cutoff_ms and visit != newest[page]]


def _delete_ids(ids):
    deleted = 0
    for start in range(0, len(ids), DELETE_BATCH):
        deleted += SyncItem.objects.filter(pk__in=ids[start:start + DELETE_BATCH]).delete()[0]
    return deleted
