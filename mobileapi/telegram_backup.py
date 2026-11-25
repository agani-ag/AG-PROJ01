"""The daily database backup, sent as a file to the Telegram chat picked on the Telegram page.

The cleanup cron (cron_views.cleanup) answers first and sends the backup once its reply has gone
out. PythonAnywhere runs web apps without threads, so the work rides on the reply's close(),
which the WSGI server calls after the last byte is sent (see cron_views.AfterReply).

At most one backup per BACKUP_EVERY: the CronLock is held that long after a good send, and freed
after a failed one so the next cron call tries again. "Backup now" on the Telegram page
sends straight away and leaves the daily one alone.

The file is the server's .sqlite3 itself, whole, named for the day it was made (syncup-YYYY-MM-DD.sqlite3);
renamed to the server's file name it goes straight back in place. It's taken with SQLite's online backup rather than a plain file copy: a copy made
while the site is writing could catch half a change.
"""
import logging
import os
import sqlite3
import tempfile
import time

from django.conf import settings
from django.db import connection
from django.utils import dateformat, timezone

from . import telegram
from .models import (AppAccount, AppChatMessage, AppConfig, AppDevice, AppLink, AppReminder, AppTelegramLog,
                     CronLock, SyncItem, TelegramChat)

logger = logging.getLogger(__name__)

BACKUP_JOB = "telegram_backup"
BACKUP_EVERY = 20 * 3600  # once a day, with room for the cron service's own drift
COPY_TIMEOUT = 60  # seconds the copy waits for other writers before giving up (it never hangs)

# What the note under each file counts, in this order.
COUNTS = [
    ("Accounts", AppAccount), ("Devices", AppDevice), ("Links", AppLink),
    ("Reminders", AppReminder), ("Chat messages", AppChatMessage), ("Synced items", SyncItem),
]


def chat_id():
    return (AppConfig.load().telegram_backup_chat_id or "").strip()


def choices():
    """Chats the bot can post in, for the Telegram page's picker."""
    return [c for c in TelegramChat.objects.order_by("title", "chat_id") if c.in_chat]


def due():
    """What the cleanup cron says about today's backup, before it has sent anything."""
    if not chat_id() or not telegram.is_configured():
        return "off"
    lock = CronLock.objects.filter(name=BACKUP_JOB).first()
    if lock and lock.locked_until > timezone.now():
        return "sent today"
    return "sending after this reply"


def run_daily():
    """The cron's backup, run after its reply. Skips quietly if today's has gone already."""
    if due() != "sending after this reply" or not CronLock.acquire(BACKUP_JOB, BACKUP_EVERY):
        return
    ok, _ = send()
    if not ok:
        CronLock.release(BACKUP_JOB)  # try again on the next cron call


def send():
    """Copy the database and send it with its note. Returns (ok, message id | error). Logged in
    the Telegram send log either way."""
    chat = chat_id()
    if not chat:
        return False, "Pick a chat for backups first."
    note = ""
    try:
        data = _snapshot()
        name = backup_name()
        note = _note(name, len(data))
        ok, out = telegram.send_document(chat, name, data, note, mime="application/vnd.sqlite3")
    except Exception as e:  # noqa: BLE001 — a backup must never break the request it rides on
        logger.exception("Database backup failed")
        ok, out = False, f"Couldn't make the backup: {e}"
    AppTelegramLog.objects.create(
        chat_id=chat, text=note or "Database backup", status="sent" if ok else "failed",
        message_id=out if ok else "", error="" if ok else str(out)[:300],
    )
    return ok, out


def db_file():
    """The server database's own file name, e.g. ag-proj01.sqlite3."""
    return os.path.basename(str(settings.DATABASES["default"]["NAME"]))


def backup_name():
    """What the file is called in Telegram: the day it was made, e.g. syncup-2026-11-25.sqlite3."""
    return f"syncup-{timezone.localtime():%Y-%m-%d}.sqlite3"


def _snapshot():
    """The live database as one complete .sqlite3 file, read on its own connection so it only sees
    finished changes. Returns its bytes."""
    name = str(connection.settings_dict["NAME"])
    deadline = time.monotonic() + COPY_TIMEOUT

    def give_up_if_stuck(status, remaining, total):
        # SQLite's backup retries a busy database forever; this is what stops it.
        if time.monotonic() > deadline:
            raise TimeoutError(f"the database stayed busy for {COPY_TIMEOUT} s")

    fd, path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    try:
        source = sqlite3.connect(name, uri=name.startswith("file:"))
        copy = sqlite3.connect(path)
        try:
            source.backup(copy, progress=give_up_if_stuck)
        finally:
            copy.close()
            source.close()
        with open(path, "rb") as f:
            return f.read()
    finally:
        os.remove(path)


def _mb(size):
    return f"{size / 1024 / 1024:.1f} MB" if size >= 100 * 1024 else f"{size / 1024:.0f} KB"


def _note(name, size):
    """The text under the file: when, how big, and what's in it."""
    now = timezone.localtime()
    counts = [f"{label} {model.objects.count():,}" for label, model in COUNTS]
    return "\n".join([
        "SyncUp database backup",
        f"{dateformat.format(now, 'd M Y, g:i A')} {now.tzname()}",
        "",
        f"{name}, {_mb(size)}",
        " · ".join(counts[:3]),
        " · ".join(counts[3:]),
    ])[:telegram.MAX_CAPTION]
