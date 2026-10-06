"""Keeps the TelegramChat registry — the answer to "which groups and chats use our bot".

Telegram has no call that lists a bot's chats, so the registry is assembled from three places:

    scan()     getUpdates — new groups the bot was added to, people who pressed Start, mentions.
               Telegram keeps unconfirmed updates ~24h, so this has to run regularly; the push
               cron (/cron/push/dispatch) calls it on every tick.
    seed()     chat ids we already hold: the send log, partner allow-lists, our report chat.
    refresh()  getChat / getChatMember on a known id — fills in the title and member count and
               reveals that the bot was removed from a group.

Nothing here raises on a Telegram failure; callers get counts and an error string to show.
"""
import logging
from datetime import datetime, timezone as dt_timezone

from django.db.models import Q
from django.utils import timezone

from . import telegram as tg
from .models import (AppConfig, AppTelegramLog, PartnerTelegramChat, TelegramChat,
                     TelegramMessage)

logger = logging.getLogger(__name__)

# How many chats one refresh pass touches; each is a Telegram round-trip, so a page click stays quick.
REFRESH_BATCH = 20


def _chat_fields(chat):
    """Map a Telegram `chat` object onto our columns."""
    title = chat.get("title") or " ".join(
        p for p in [chat.get("first_name"), chat.get("last_name")] if p
    ) or (chat.get("username") or "")
    return {
        "title": title[:200],
        "chat_type": (chat.get("type") or "")[:20],
        "username": (chat.get("username") or "")[:64],
    }


def _upsert(chat, *, activity=None, status=None, added_by=None):
    """Create or update one registry row from a Telegram `chat` object. Returns (row, created)."""
    chat_id = str(chat.get("id") or "").strip()
    if not chat_id:
        return None, False
    fields = _chat_fields(chat)
    if activity:
        fields["last_activity_at"] = activity
    if status:
        fields["status"] = status
    if added_by:
        fields["added_by"] = added_by[:120]
    row, created = TelegramChat.objects.get_or_create(chat_id=chat_id, defaults=fields)
    if not created:
        # Keep whatever Telegram just told us; don't wipe a known title with a blank one.
        changed = []
        for key, value in fields.items():
            if value in ("", None):
                continue
            if getattr(row, key) != value:
                setattr(row, key, value)
                changed.append(key)
        if changed:
            row.save(update_fields=changed)
    return row, created


def _person(user):
    """'Priya Raman (@priya)' from an update's `from` object."""
    if not user:
        return ""
    name = " ".join(p for p in [user.get("first_name"), user.get("last_name")] if p)
    handle = user.get("username")
    return f"{name} (@{handle})" if name and handle else (name or (f"@{handle}" if handle else ""))


# What a message carries besides text, in the order we check for it.
MEDIA_KINDS = [
    ("photo", "Photo"), ("video", "Video"), ("document", "Document"), ("voice", "Voice message"),
    ("audio", "Audio"), ("sticker", "Sticker"), ("animation", "GIF"), ("video_note", "Video note"),
    ("location", "Location"), ("contact", "Contact"), ("poll", "Poll"),
]


def _media_type(msg):
    for key, _label in MEDIA_KINDS:
        if msg.get(key):
            return key
    return ""


def _message_text(msg):
    """Text, or a caption, or a short stand-in for something we can't show."""
    text = msg.get("text") or msg.get("caption") or ""
    if text:
        return text
    if msg.get("location"):
        loc = msg["location"]
        return f"\U0001F4CD {loc.get('latitude')}, {loc.get('longitude')}"
    if msg.get("contact"):
        c = msg["contact"]
        return " ".join(p for p in [c.get("first_name"), c.get("phone_number")] if p)
    if msg.get("poll"):
        return (msg["poll"].get("question") or "")[:200]
    return ""


def ingest(update):
    """Fold one Telegram update into the registry (and store the message, if it is one).

    Shared by the webhook and the cron scan, so both paths behave identically.
    Returns {"chat": row|None, "created": bool, "message": TelegramMessage|None}.
    """
    member = update.get("my_chat_member")
    msg = update.get("message") or update.get("channel_post") or update.get("edited_message")
    obj = member or msg or update.get("chat_member") or {}
    chat = obj.get("chat") or {}
    if not chat:
        return {"chat": None, "created": False, "message": None}

    now = timezone.now()
    status = added_by = None
    if member:
        # The bot's own membership changed — "added to"/"removed from" a group.
        status = ((member.get("new_chat_member") or {}).get("status") or "")[:20] or None
        added_by = _person(member.get("from"))
    row, created = _upsert(chat, activity=now, status=status, added_by=added_by)
    if row is None:
        return {"chat": None, "created": False, "message": None}

    stored = _store_incoming(row, msg) if msg else None
    return {"chat": row, "created": created, "message": stored}


def _store_incoming(row, msg):
    """Save a received message. Ignores duplicates (a webhook retry delivers the same update)."""
    sent_at = timezone.now()
    if msg.get("date"):
        sent_at = datetime.fromtimestamp(int(msg["date"]), tz=dt_timezone.utc)
    reply = msg.get("reply_to_message") or {}
    stored, created = TelegramMessage.objects.get_or_create(
        chat=row, message_id=msg.get("message_id"),
        defaults={
            "direction": "in",
            "from_name": _person(msg.get("from"))[:120],
            "from_user_id": str((msg.get("from") or {}).get("id") or "")[:32],
            "text": _message_text(msg),
            "media_type": _media_type(msg),
            "reply_to_message_id": reply.get("message_id"),
            "reply_to_text": (_message_text(reply) or _media_type(reply))[:200],
            "sent_at": sent_at,
        },
    )
    if not created:
        return stored
    TelegramChat.objects.filter(pk=row.pk).update(last_message_at=sent_at, last_activity_at=sent_at)
    trim(row)
    return stored


def store_outgoing(row, text, message_id=None, sent_by=None, reply_to=None):
    """Record a message the console sent, so it shows in the thread."""
    reply = reply_to or {}
    stored = TelegramMessage.objects.create(
        chat=row, message_id=message_id, direction="out", text=text,
        reply_to_message_id=reply.get("message_id"),
        reply_to_text=(reply.get("text") or "")[:200],
        sent_at=timezone.now(), sent_by=sent_by,
    )
    TelegramChat.objects.filter(pk=row.pk).update(
        last_message_at=stored.sent_at, last_activity_at=stored.sent_at, last_read_at=stored.sent_at,
    )
    trim(row)
    return stored


def trim(row, keep=None):
    """Rolling window: keep only the newest N messages in this chat (0 = keep all).

    The age-based window lives in cleanup.py and runs on the retention job; this one runs on every
    new message so a busy group can never balloon between cleanups.
    """
    if keep is None:
        keep = AppConfig.load().telegram_keep_per_chat
    if not keep:
        return 0
    ids = list(
        TelegramMessage.objects.filter(chat=row).order_by("-sent_at", "-id")
        .values_list("id", flat=True)[keep:keep + 500]
    )
    if not ids:
        return 0
    return TelegramMessage.objects.filter(id__in=ids).delete()[0]


def scan():
    """Pull new updates and fold them into the registry.

    Returns {"new": n, "seen": n, "updates": n, "error": str}. `new` counts chats we had never
    heard of, `seen` chats we already knew.
    """
    if not tg.is_configured():
        return {"new": 0, "seen": 0, "messages": 0, "updates": 0, "error": "Telegram bot is not configured"}

    cfg = AppConfig.load()
    updates, error = tg.get_updates(offset=cfg.telegram_updates_offset)
    if error:
        return {"new": 0, "seen": 0, "messages": 0, "updates": 0, "error": error}

    new = seen = messages = 0
    highest = cfg.telegram_updates_offset
    for upd in updates:
        highest = max(highest, int(upd.get("update_id") or 0))
        result = ingest(upd)
        new += 1 if result["created"] else 0
        seen += 0 if result["created"] else 1
        messages += 1 if result["message"] else 0

    if highest and highest >= cfg.telegram_updates_offset:
        # +1 confirms everything up to and including the last update we just read.
        cfg.telegram_updates_offset = highest + 1
        cfg.save(update_fields=["telegram_updates_offset"])
    return {"new": new, "seen": seen, "messages": messages, "updates": len(updates), "error": ""}


def seed():
    """Add chat ids we already hold (send log, partner allow-lists, our report chat) as rows.

    Gives an immediate list of the chats using the bot, without waiting for someone to post.
    Returns {"added": n}.
    """
    known = set(TelegramChat.objects.values_list("chat_id", flat=True))
    ids = set()
    ids.update(AppTelegramLog.objects.values_list("chat_id", flat=True).distinct())
    ids.update(PartnerTelegramChat.objects.values_list("chat_id", flat=True).distinct())
    admin_chat = (AppConfig.load().telegram_admin_chat_id or "").strip()
    if admin_chat:
        ids.add(admin_chat)

    rows = [
        TelegramChat(chat_id=cid.strip(), note="From our records")
        for cid in ids
        if (cid or "").strip() and cid.strip() not in known
    ]
    TelegramChat.objects.bulk_create(rows, ignore_conflicts=True)
    return {"added": len(rows)}


def refresh(chat_ids=None, limit=REFRESH_BATCH, older_than=None):
    """Ask Telegram about known chats: title, type, member count and whether the bot is still in.

    With no `chat_ids`, refreshes the ones checked longest ago (oldest first), `limit` at a time.
    `older_than` skips chats already checked since that moment, which is what lets the page loop
    through everything a batch at a time without repeating itself. Every row touched gets
    last_checked_at set, even on failure, so such a loop always terminates.
    Returns {"checked": n, "gone": n, "error": str} — `gone` counts chats the bot can no longer
    post to (removed, left, or deleted).
    """
    if not tg.is_configured():
        return {"checked": 0, "gone": 0, "error": "Telegram bot is not configured"}

    qs = TelegramChat.objects.all()
    if chat_ids:
        qs = qs.filter(chat_id__in=[str(c) for c in chat_ids])
    else:
        if older_than:
            qs = qs.filter(Q(last_checked_at__isnull=True) | Q(last_checked_at__lt=older_than))
        qs = qs.order_by("last_checked_at")  # NULLs first on SQLite: never-checked rows go first
    rows = list(qs[:limit])

    now = timezone.now()
    checked = gone = 0
    for row in rows:
        chat, error = tg.get_chat(row.chat_id)
        fields = {"last_checked_at": now, "last_error": error[:200]}
        if chat:
            fields.update(_chat_fields(chat))
            is_private = fields["chat_type"] == "private"
            fields["status"] = "member" if is_private else tg.get_bot_status(row.chat_id)
            if not is_private:
                count = tg.get_chat_member_count(row.chat_id)
                if count is not None:
                    fields["member_count"] = count
        else:
            # "chat not found" / "bot was kicked" are permanent; a timeout is not, so keep the
            # old status and just record the error.
            lowered = error.lower()
            if "not found" in lowered or "kicked" in lowered or "forbidden" in lowered:
                fields["status"] = "kicked"
        for key, value in fields.items():
            setattr(row, key, value)
        row.save(update_fields=list(fields.keys()))
        checked += 1
        gone += 0 if row.in_chat else 1
    return {"checked": checked, "gone": gone, "error": ""}


def partner_map():
    """{chat_id: [partner names]} — who is allowed to send to each chat, for the registry table."""
    out = {}
    for chat_id, name in PartnerTelegramChat.objects.values_list("chat_id", "partner__name"):
        out.setdefault(chat_id, []).append(name)
    return out


def pending_recheck(older_than):
    """How many chats still need re-checking in a pass that began at `older_than`."""
    return TelegramChat.objects.filter(
        Q(last_checked_at__isnull=True) | Q(last_checked_at__lt=older_than)
    ).count()
