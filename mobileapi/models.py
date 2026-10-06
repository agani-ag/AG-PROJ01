"""
Data models for the standalone SyncUp mobile API (`com.agani.syncup`).

Fully isolated from the existing `syncup` app: no foreign keys to any existing
table, its own account/credential system (not Django `User`), its own token auth.
See md/syncup-android-backend-plan.md.
"""
import json
import hashlib
import secrets
from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.core.validators import MinValueValidator
from django.db import IntegrityError, models, transaction
from django.utils import timezone

# How long an issued bearer token stays valid. After this the app gets a 401 and
# transparently returns the user to the login screen (see the app's 401 handling).
TOKEN_TTL_DAYS = 60


# =============== App account (mobile-only credentials) ===============
ACCOUNT_SOURCE_CHOICES = [
    ("admin", "Admin"),
    ("partner", "Partner"),
    ("self", "Self sign-up"),
]


class AppAccount(models.Model):
    """A mobile app user. Separate from Django `User` (which is admin-only).

    Signs in with a password plus ONE of: email, phone (India, stored +91XXXXXXXXXX) or username.
    Each is unique when set. Every account keeps at least one of email / phone; a self sign-up or a
    partner-added user may have only a phone. The username is always the person's own, added later
    from the app.
    """

    name = models.CharField(max_length=100)
    email = models.EmailField(unique=True, null=True, blank=True)
    phone = models.CharField(max_length=16, unique=True, null=True, blank=True)
    username = models.CharField(max_length=30, unique=True, null=True, blank=True)
    # How the account came to exist (drives the Accounts filter and a few partner rules).
    source = models.CharField(max_length=10, choices=ACCOUNT_SOURCE_CHOICES, default="admin")
    password = models.CharField(max_length=128)  # PBKDF2 hash, never plaintext
    is_active = models.BooleanField(default=True)
    # Support agent: when on, this user's in-app Chat opens the full support inbox (all users'
    # conversations, excluding their own) and their replies appear to end users as "Admin".
    admin_chat_mode = models.BooleanField(
        default=False,
        help_text="Support agent — their app Chat opens the admin inbox instead of a personal chat.",
    )
    show_general_links = models.BooleanField(
        default=True,
        help_text="Include the shared 'general' links in this user's list. Turn off for single-link "
                  "(kiosk) users so their one link still auto-opens.",
    )
    # No longer used: Radio is on/off for everyone from the Radio page (AppConfig.radio_enabled).
    # Kept unchanged so no migration is needed; safe to drop later.
    radio_enabled = models.BooleanField(
        default=True,
        help_text="Show the in-app Radio feature for this user (only when the master radio switch is on too).",
    )
    chat_enabled = models.BooleanField(
        default=True,
        help_text="Show Chat with admin for this user (only when the master chat switch is on too).",
    )
    # Partner-first accounts sign in with the partner password until the user sets their own; while
    # this is on, a password reset by that partner also resets the SyncUp sign-in.
    partner_signin = models.BooleanField(default=False)
    # The partner that CREATED this account (partner-first). Blank = admin or self sign-up. Partner
    # access itself lives on PartnerConnection (one per partner); this only records the origin.
    partner = models.ForeignKey(
        "AppPartner", on_delete=models.SET_NULL, null=True, blank=True, related_name="accounts",
    )
    last_login = models.DateTimeField(null=True, blank=True)
    # Last time this user's app polled the chat screen — used to skip the admin-reply push
    # while they're actively looking at the chat.
    chat_last_seen_at = models.DateTimeField(null=True, blank=True)
    # Short-lived "user is typing" signal (set a few seconds ahead while they type).
    chat_typing_until = models.DateTimeField(null=True, blank=True)
    # Short-lived "admin is typing" signal (shown to the user in the app chat).
    chat_admin_typing_until = models.DateTimeField(null=True, blank=True)
    # Last time the admin had THIS user's chat open in the console (drives the "admin active" dot).
    admin_last_seen_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def set_password(self, raw_password):
        self.password = make_password(raw_password)

    def check_password(self, raw_password):
        return check_password(raw_password, self.password)

    def is_active_on_chat(self, window_seconds=15):
        """True if the user's chat screen polled within the last `window_seconds` (i.e. they're
        currently looking at the chat), so an admin reply doesn't need a push notification."""
        if not self.chat_last_seen_at:
            return False
        return (timezone.now() - self.chat_last_seen_at).total_seconds() < window_seconds

    def is_typing(self):
        """True while the user is actively typing (their app pinged within the TTL)."""
        return bool(self.chat_typing_until and self.chat_typing_until > timezone.now())

    def is_admin_typing(self):
        """True while the admin is actively typing a reply (shown to the user in the app)."""
        return bool(self.chat_admin_typing_until and self.chat_admin_typing_until > timezone.now())

    def is_admin_online(self, window_seconds=30):
        """True if the admin currently has this user's chat open in the console."""
        if not self.admin_last_seen_at:
            return False
        return (timezone.now() - self.admin_last_seen_at).total_seconds() < window_seconds

    def save(self, *args, **kwargs):
        # Blank identifiers are stored as NULL so the unique constraints ignore them.
        self.email = (self.email or "").strip().lower() or None
        self.phone = (self.phone or "").strip() or None
        self.username = (self.username or "").strip().lower() or None
        if self.name:
            self.name = self.name.strip()
        super().save(*args, **kwargs)

    @property
    def login_label(self):
        """The best identifier to show for this account (email, then phone, then @username)."""
        return self.email or self.phone or (f"@{self.username}" if self.username else f"#{self.pk}")

    def __str__(self):
        return f"{self.name} <{self.login_label}>"


# =============== Bearer token ===============
class AppAuthToken(models.Model):
    """Opaque bearer token issued at login; sent as `Authorization: Bearer <key>`."""

    key = models.CharField(max_length=40, unique=True, db_index=True)
    account = models.ForeignKey(AppAccount, on_delete=models.CASCADE, related_name="tokens")
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked = models.BooleanField(default=False)
    expires_at = models.DateTimeField(null=True, blank=True)
    # The install this token signed in on (set at login or when the device registers), so the admin
    # can sign out one device. Blank for older tokens — "sign out all" still covers those.
    device_id = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["-created_at"]

    @classmethod
    def issue(cls, account, ttl_days=TOKEN_TTL_DAYS, device_id=""):
        return cls.objects.create(
            key=secrets.token_hex(20),
            account=account,
            expires_at=timezone.now() + timedelta(days=ttl_days),
            device_id=(device_id or "")[:255],
        )

    @property
    def is_valid(self):
        if self.revoked:
            return False
        if self.expires_at and self.expires_at < timezone.now():
            return False
        return True

    def __str__(self):
        return f"{self.account.login_label} · {self.key[:8]}…"


# =============== Partner (B2B provisioning API) ===============
class AppPartner(models.Model):
    """A partner that can create/manage its own users + links via /partner/v1/.

    Authenticates with an API key sent as `Authorization: Bearer <key>`. Only the SHA-256 hash of
    the key is stored, so the raw key is shown once at creation and can't be recovered — only
    regenerated. Every Partner-API request is scoped to `self.accounts` (see AppAccount.partner)."""

    name = models.CharField(max_length=100)
    api_key_hash = models.CharField(max_length=64, unique=True, db_index=True)  # sha256 hex
    # Raw HMAC secret the partner uses to verify our action-callback signatures (stored plaintext
    # because we must recompute the HMAC on every callback). Shown to the admin, not the public.
    signing_secret = models.CharField(max_length=64, default="")
    contact_email = models.EmailField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    rate_limit_per_min = models.PositiveIntegerField(
        default=120, help_text="Max Partner-API requests per minute for this partner.",
    )
    # Telegram relay: by default a partner may only send to the chats listed in its
    # PartnerTelegramChat allowlist, so one partner can't message another partner's group.
    telegram_allow_any = models.BooleanField(
        default=False,
        help_text="Let this partner send Telegram messages to ANY chat our bot can reach "
                  "(skips its allowed-chats list). Leave off unless you trust it completely.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name"]

    @staticmethod
    def hash_key(raw_key):
        return hashlib.sha256(raw_key.encode()).hexdigest()

    @classmethod
    def issue(cls, name, contact_email=""):
        """Create a partner; returns (partner, raw_key). The raw key is shown ONCE."""
        raw_key = cls._new_key()
        partner = cls.objects.create(
            name=name.strip(), contact_email=(contact_email or "").strip() or None,
            api_key_hash=cls.hash_key(raw_key),
            signing_secret=secrets.token_urlsafe(24),
        )
        return partner, raw_key

    def regenerate_key(self):
        """Roll the API key (old one stops working immediately). Returns the new raw key."""
        raw_key = self._new_key()
        self.api_key_hash = self.hash_key(raw_key)
        self.save(update_fields=["api_key_hash"])
        return raw_key

    @staticmethod
    def _new_key():
        return "sk_" + secrets.token_urlsafe(32)

    def __str__(self):
        return self.name


# =============== Telegram chats a partner may send to ===============
class PartnerTelegramChat(models.Model):
    """One Telegram chat id a partner is allowed to send to (its own group or a person's DM).

    The partner tells us the chat id after adding our bot to its group; the admin saves it here
    from the Telegram page. Sends to any other chat are refused, so partners stay isolated from
    each other's groups. A partner with `telegram_allow_any` on skips this list entirely.
    """

    partner = models.ForeignKey(
        AppPartner, on_delete=models.CASCADE, related_name="telegram_chats",
    )
    chat_id = models.CharField(max_length=64)
    label = models.CharField(
        max_length=120, blank=True, default="",
        help_text="What this chat is, e.g. “Acme ops group”. Shown in the logs instead of the raw id.",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["partner__name", "label", "chat_id"]
        constraints = [
            models.UniqueConstraint(fields=["partner", "chat_id"], name="uniq_partner_telegram_chat"),
        ]
        indexes = [models.Index(fields=["partner", "chat_id"])]

    @classmethod
    def allows(cls, partner, chat_id):
        """True if this partner may send to chat_id."""
        if partner is None:
            return False
        if partner.telegram_allow_any:
            return True
        return cls.objects.filter(
            partner=partner, chat_id=str(chat_id).strip(), is_active=True,
        ).exists()

    @classmethod
    def touch(cls, partner, chat_id):
        """Record that a chat was just used (shown on the Telegram page)."""
        cls.objects.filter(partner=partner, chat_id=str(chat_id).strip()).update(
            last_used_at=timezone.now(),
        )

    def __str__(self):
        return f"{self.partner.name} → {self.label or self.chat_id}"


# =============== Every chat our Telegram bot is in (the registry) ===============
class TelegramChat(models.Model):
    """A group, channel or person our bot has been seen in — "who is using this bot".

    Telegram has no "list my chats" call. A bot only learns a chat exists when an update arrives
    (it was added to a group, someone messaged or mentioned it) or when we ask getChat about an id
    we already hold. So this table is built from three sources and kept here permanently:

      * scan  — getUpdates, run from the Telegram page and the push cron;
      * seed  — chat ids we already know from the send log and the partner allow-lists;
      * refresh — getChat / getChatMember on a known id, which also tells us whether the bot is
                  still a member or was removed.
    """

    STATUS = [
        ("member", "Member"),
        ("administrator", "Administrator"),
        ("creator", "Owner"),
        ("restricted", "Restricted"),
        ("left", "Left"),
        ("kicked", "Removed"),
        ("unknown", "Unknown"),
    ]
    IN_CHAT = ("member", "administrator", "creator", "restricted")

    chat_id = models.CharField(max_length=64, unique=True)
    title = models.CharField(max_length=200, blank=True, default="")
    chat_type = models.CharField(max_length=20, blank=True, default="")  # private/group/supergroup/channel
    username = models.CharField(max_length=64, blank=True, default="")   # public @name, if any
    member_count = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS, default="unknown")
    # Who added our bot (from the my_chat_member update), for groups we saw being set up.
    added_by = models.CharField(max_length=120, blank=True, default="")
    note = models.CharField(max_length=200, blank=True, default="")
    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_activity_at = models.DateTimeField(null=True, blank=True)   # last update seen from this chat
    last_checked_at = models.DateTimeField(null=True, blank=True)    # last getChat refresh
    last_error = models.CharField(max_length=200, blank=True, default="")
    # Conversation state for the inbox.
    last_message_at = models.DateTimeField(null=True, blank=True)
    last_read_at = models.DateTimeField(null=True, blank=True)        # when an admin last opened it

    class Meta:
        ordering = ["-last_activity_at", "title", "chat_id"]
        indexes = [models.Index(fields=["-last_activity_at"]), models.Index(fields=["status"])]

    @property
    def display_name(self):
        return self.title or (f"@{self.username}" if self.username else self.chat_id)

    @property
    def in_chat(self):
        """True while the bot can still post here."""
        return self.status in self.IN_CHAT

    @property
    def is_private(self):
        return self.chat_type == "private"

    def __str__(self):
        return f"{self.display_name} ({self.chat_id})"


# =============== One Telegram message, in or out ===============
class TelegramMessage(models.Model):
    """A message our bot received or sent, kept so the console can show a conversation.

    A bot cannot fetch history — the Bot API has no such call — so a message exists here only if
    it was captured as it arrived (webhook, or the cron's getUpdates). Two trim rules keep this
    from growing: at most `telegram_keep_per_chat` messages per chat, and nothing older than the
    chat retention window (see cleanup.py). `reply_to_text` holds a snippet of what was replied
    to, so a reply still reads correctly after its parent has been trimmed away.
    """

    DIRECTION = [("in", "Received"), ("out", "Sent")]
    # Labels for the kinds of attachment we recognise (detection order lives in telegram_chats.py).
    MEDIA_LABELS = {
        "photo": "Photo", "video": "Video", "document": "Document", "voice": "Voice message",
        "audio": "Audio", "sticker": "Sticker", "animation": "GIF", "video_note": "Video note",
        "location": "Location", "contact": "Contact", "poll": "Poll",
    }

    chat = models.ForeignKey(TelegramChat, on_delete=models.CASCADE, related_name="messages")
    message_id = models.BigIntegerField(null=True, blank=True)  # Telegram's id within the chat
    direction = models.CharField(max_length=3, choices=DIRECTION)
    from_name = models.CharField(max_length=120, blank=True, default="")   # who wrote it (incoming)
    from_user_id = models.CharField(max_length=32, blank=True, default="")
    text = models.TextField(blank=True, default="")
    media_type = models.CharField(max_length=20, blank=True, default="")   # photo, document, voice…
    reply_to_message_id = models.BigIntegerField(null=True, blank=True)
    reply_to_text = models.CharField(max_length=200, blank=True, default="")
    sent_at = models.DateTimeField()                                        # Telegram's timestamp
    created_at = models.DateTimeField(auto_now_add=True)
    # Which admin sent it from the console (outgoing only).
    sent_by = models.ForeignKey(
        "auth.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )

    class Meta:
        ordering = ["sent_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["chat", "message_id"],
                condition=models.Q(message_id__isnull=False),
                name="uniq_telegram_chat_message",
            ),
        ]
        indexes = [models.Index(fields=["chat", "-sent_at"])]

    @property
    def media_label(self):
        if not self.media_type:
            return ""
        return self.MEDIA_LABELS.get(self.media_type, self.media_type.replace("_", " ").capitalize())

    @property
    def preview(self):
        return self.text or self.media_label

    def __str__(self):
        return f"{self.direction} {self.chat_id}: {self.text[:40]}"


# =============== Partner connection (a partner's access key on an account) ===============
PARTNER_CONNECTION_STATUS = [
    ("not_enabled", "Not enabled"),
    ("enabled", "Enabled"),
    ("disabled", "Disabled by user"),
]

# Wrong partner passwords allowed on the app's Partners page before a cool-down.
PARTNER_MAX_ATTEMPTS = 5
PARTNER_LOCK_MINUTES = 15


class PartnerConnection(models.Model):
    """One partner's link to one SyncUp account. The account belongs to the person; each partner
    that adds them gets a connection with its OWN partner password. The user enables a partner in
    the app by entering that password, and can disable it any time. Only an enabled connection
    (that the partner hasn't suspended) lets the partner's links, prompts and pushes through."""

    partner = models.ForeignKey(AppPartner, on_delete=models.CASCADE, related_name="connections")
    account = models.ForeignKey(AppAccount, on_delete=models.CASCADE, related_name="partner_connections")
    # The partner's own id for this user (their system's key), unique within the partner.
    external_id = models.CharField(max_length=128, null=True, blank=True)
    password = models.CharField(max_length=128)  # hash of the partner password
    status = models.CharField(max_length=12, choices=PARTNER_CONNECTION_STATUS, default="not_enabled")
    # The partner can suspend its own access (Partner API is_active / DELETE) without touching the account.
    partner_active = models.BooleanField(default=True)
    failed_attempts = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    enabled_at = models.DateTimeField(null=True, blank=True)
    disabled_at = models.DateTimeField(null=True, blank=True)
    # An email / phone the partner sent that the person's own account is missing. It fills that
    # blank when the person turns this partner on (the partner password shows it's really them),
    # then clears — so a number shared in a family never picks up someone else's email.
    pending_email = models.EmailField(null=True, blank=True)
    pending_phone = models.CharField(max_length=16, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["partner__name"]
        constraints = [
            models.UniqueConstraint(fields=["partner", "account"], name="uniq_partner_account"),
            models.UniqueConstraint(
                fields=["partner", "external_id"],
                condition=models.Q(external_id__isnull=False),
                name="uniq_connection_external_id",
            ),
        ]

    def set_password(self, raw_password):
        self.password = make_password(raw_password)

    def check_password(self, raw_password):
        return check_password(raw_password, self.password)

    @property
    def is_live(self):
        """True when this partner can currently reach the user (enabled, not suspended)."""
        return self.status == "enabled" and self.partner_active and self.partner.is_active

    @property
    def is_locked(self):
        return bool(self.locked_until and self.locked_until > timezone.now())

    @property
    def pending_fields(self):
        return [f for f in ("email", "phone") if getattr(self, f"pending_{f}")]

    def fill_pending(self):
        """Fill the account's blank email / phone with what this partner sent (see `pending_*`).
        A value someone else has taken meanwhile, or a blank the person has filled themselves, is
        skipped. Returns the fields filled. Saves the account; the caller saves the connection."""
        a = self.account
        filled = []
        for field in ("email", "phone"):
            value = getattr(self, f"pending_{field}")
            setattr(self, f"pending_{field}", None)
            if value and not getattr(a, field) and \
                    not AppAccount.objects.filter(**{field: value}).exclude(id=a.id).exists():
                setattr(a, field, value)
                filled.append(field)
        if filled:
            try:
                with transaction.atomic():
                    a.save(update_fields=[*filled, "updated_at"])
            except IntegrityError:  # taken in the same instant — leave the account as it was
                a.refresh_from_db()
                filled = []
        return filled

    def __str__(self):
        return f"{self.partner.name} ↔ {self.account.login_label} ({self.status})"


# =============== Action / verification request (partner ↔ user bridge) ===============
class AppActionRequest(models.Model):
    """A partner-triggered action the target user completes on their phone (OTP shown, code
    entered, or a number selected). We deliver it via push, capture the user's response, and POST
    the signed result to the partner's callback_url. We DON'T verify anything — the partner does."""

    ACTION_TYPES = [
        ("otp", "OTP deliver"), ("code", "Code entry"), ("number", "Select a number"),
        ("notice", "Info + acknowledge"), ("approve", "Approve / Reject"),
    ]
    STATUS = [("pending", "Pending"), ("completed", "Completed"), ("expired", "Expired")]

    # Null = admin-initiated test (from the Test Verify console) — no partner, no callback.
    partner = models.ForeignKey(
        AppPartner, on_delete=models.CASCADE, related_name="action_requests", null=True, blank=True,
    )
    account = models.ForeignKey(AppAccount, on_delete=models.CASCADE, related_name="action_requests")
    action_type = models.CharField(max_length=10, choices=ACTION_TYPES)
    title = models.CharField(max_length=120)
    message = models.CharField(max_length=300, blank=True)
    # Type-specific data: otp → {"code": "123456"}; code → {"length": 6}; number → {"numbers": [..]};
    # notice → {"body": "...", "cta_url": "https://…", "cta_label": "View details"} (long text lives
    # here, not in `message`, so it isn't capped at 300 chars); approve → optional
    # {"approve_label": "Approve", "reject_label": "Reject"}.
    params = models.JSONField(default=dict, blank=True)
    # Where we POST the signed result. Blank for admin tests (result is just recorded here).
    callback_url = models.URLField(max_length=500, blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default="pending")
    response = models.JSONField(null=True, blank=True)   # what the user entered/selected
    delivered = models.PositiveIntegerField(default=0)   # device push count
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["account", "status"])]

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    def __str__(self):
        return f"{self.action_type} → {self.account.login_label} ({self.status})"


# Icon names understood by the app (empty = auto-pick from the title).
APP_ICON_CHOICES = [
    ("", "Auto (from title)"),
    ("link", "Link"),
    ("globe", "Globe / Web"),
    ("camera", "Camera"),
    ("location", "Location"),
    ("upload", "Upload / File"),
    ("youtube", "Video / YouTube"),
    ("notification", "Notification / Bell"),
    ("speed", "Speed / Bolt"),
    ("newtab", "New tab / Popup"),
    ("home", "Home"),
    ("cart", "Cart / Shop"),
    ("person", "Person / Account"),
    ("settings", "Settings"),
    ("document", "Document"),
    ("phone", "Phone / Call"),
    ("image", "Image"),
    ("star", "Star"),
]


# =============== Per-account links (the URL list shown in the app) ===============
class AppLink(models.Model):
    # Null = a GENERAL link, shown to every account whose show_general_links is on. A set account
    # = a personal link for just that user.
    account = models.ForeignKey(
        AppAccount, on_delete=models.CASCADE, related_name="links", null=True, blank=True,
    )
    title = models.CharField(max_length=100)
    url = models.URLField(max_length=500)
    description = models.CharField(max_length=200, null=True, blank=True)
    icon = models.CharField(max_length=50, null=True, blank=True, choices=APP_ICON_CHOICES)
    is_active = models.BooleanField(default=True)
    # When on, the app injects window.SyncUp={token} into this page so the site can push
    # notifications to this exact user (via POST /app/v1/partner/notify). Uncheck to revoke.
    notify_token_enabled = models.BooleanField(
        default=False,
        help_text="Inject a SyncUp notification token (window.SyncUp.token) so this site can push to this user.",
    )
    # A partner's own key for this link (their system's id). Unique per account, so a partner can
    # replace-by-key: upserting a user's link with the same key updates it instead of duplicating.
    external_id = models.CharField(max_length=128, null=True, blank=True)
    # The partner that owns this link (blank = SyncUp admin link). A partner link is shown only
    # while that partner's connection on the account is enabled, grouped under the partner's name.
    partner = models.ForeignKey(
        AppPartner, on_delete=models.CASCADE, null=True, blank=True, related_name="links",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["title"]  # links always shown ascending by title
        indexes = [
            models.Index(fields=["account", "is_active"]),  # speeds the per-account active-link query
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["account", "external_id"],
                condition=models.Q(external_id__isnull=False),
                name="uniq_account_link_external_id",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.title:
            self.title = self.title.strip()
        if self.url:
            self.url = self.url.strip()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.title} ({self.account.login_label if self.account_id else 'general'})"


# =============== FCM device registration ===============
class AppDevice(models.Model):
    """One app install. `account` is whoever is signed in on it right now (blank = signed out);
    signing out detaches the account but the install keeps receiving public broadcasts."""

    account = models.ForeignKey(
        AppAccount, on_delete=models.SET_NULL, null=True, blank=True, related_name="devices",
    )
    device_id = models.CharField(max_length=255, unique=True)
    fcm_token = models.TextField()
    platform = models.CharField(max_length=50, default="android")
    app_version = models.CharField(max_length=50, null=True, blank=True)
    last_seen = models.DateTimeField(default=timezone.now)
    # When this device last pulled the reminder list (its background/foreground reminder sync).
    last_reminder_sync_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    # ---- Install registry (every install says hello, signed in or not) ----
    # Issued on the first hello; later hellos for this device_id must present it, so nobody can
    # take over another install's row by guessing its id. Blank for rows from older app versions.
    device_secret = models.CharField(max_length=64, blank=True, default="")
    installed_at = models.DateTimeField(default=timezone.now)
    converted_at = models.DateTimeField(null=True, blank=True, help_text="First time an account signed in here.")
    os_version = models.CharField(max_length=40, blank=True, default="")
    locale = models.CharField(max_length=20, blank=True, default="")
    time_zone = models.CharField(max_length=60, blank=True, default="")
    country = models.CharField(max_length=4, blank=True, default="")  # from the connection; IP never stored
    device_model = models.CharField(max_length=80, blank=True, default="")
    webview_version = models.CharField(max_length=40, blank=True, default="")
    install_source = models.CharField(max_length=60, blank=True, default="")
    notifications_allowed = models.BooleanField(null=True, blank=True)
    updates_enabled = models.BooleanField(default=True, help_text="The app's 'SyncUp updates' switch.")

    class Meta:
        ordering = ["-last_seen"]

    @property
    def app_version_code(self):
        """The numeric app version (e.g. '6' → 6); 0 when unknown."""
        try:
            return int(str(self.app_version or "").split(".")[0])
        except ValueError:
            return 0

    def __str__(self):
        who = self.account.login_label if self.account_id else "signed out"
        return f"{who} · {self.device_id}"


# =============== Server-driven config (singleton) ===============
class AppConfig(models.Model):
    """Single-row config served by GET /app/v1/config (admin-managed)."""

    min_supported_version = models.IntegerField(default=1)
    # Android application id — used to build the Play Store / open-app links (e.g. the update screen).
    android_package_name = models.CharField(
        max_length=150, blank=True, default="com.agani.syncup",
        help_text="The Android app's package name (application id), e.g. com.agani.syncup.",
    )
    support_email = models.EmailField(null=True, blank=True)
    support_phone = models.CharField(max_length=30, null=True, blank=True)
    # Privacy policy page details (shown on the public /privacy/ page)
    privacy_company_name = models.CharField(max_length=120, default="SyncUp")
    privacy_effective_date = models.CharField(max_length=40, default="10 August 2026")
    privacy_contact_email = models.EmailField(
        null=True, blank=True, help_text="Privacy contact. Falls back to the support email if blank.",
    )
    announcement_active = models.BooleanField(default=False)
    announcement_title = models.CharField(max_length=120, null=True, blank=True)
    announcement_message = models.TextField(null=True, blank=True)
    announcement_fullscreen = models.BooleanField(
        default=False,
        help_text="Show the announcement as a full screen (like the update screen) instead of a banner.",
    )
    announcement_blocking = models.BooleanField(
        default=False,
        help_text="Full-screen only: no dismiss button; shows every launch until turned off "
                  "(use for maintenance/outage notices).",
    )
    feature_flags = models.JSONField(default=dict, blank=True)
    # Global on/off for the in-app chat. When off, the chat button is hidden for everyone
    # (regular users and support-agent/admin-chat-mode accounts alike).
    chat_enabled = models.BooleanField(
        default=True,
        help_text="Show the in-app chat button for everyone. Turn off to hide chat across the app.",
    )
    signup_enabled = models.BooleanField(
        default=False,
        help_text="Show 'Create account' in the app. Off = only admins and partners create accounts.",
    )
    # Master defaults for the Android/FCM block applied to every push (the send form can override
    # per-send). Stored as the friendly option keys the Push form reads/writes — see
    # fcm.build_android_config. Empty dict = plain notification (current behaviour).
    fcm_push_defaults = models.JSONField(default=dict, blank=True)
    # How often the external cron service calls /cron/push/dispatch. The dispatcher works at any
    # cadence; this only sets the shortest a server (cron) push may repeat and the banner text.
    # Change it here (no redeploy) to match whatever the cron is actually set to.
    cron_dispatch_interval_minutes = models.PositiveIntegerField(
        default=15, validators=[MinValueValidator(1)],
        help_text="Minutes — match this to your external cron's schedule (any value). Sets the "
                  "shortest a server push can repeat.",
    )

    # ---- Data-retention windows (days) for the cleanup job. 0 = never prune that table. ----
    # Expired auth tokens and expired sessions are always removed regardless of these.
    cleanup_log_days = models.PositiveIntegerField(
        default=30, help_text="Delete push/notification logs older than this many days (0 = keep all).",
    )
    cleanup_chat_days = models.PositiveIntegerField(
        default=30, help_text="Delete chat messages older than this (unread messages are always kept; 0 = keep all).",
    )
    cleanup_inactive_device_days = models.PositiveIntegerField(
        default=30, help_text="Delete deactivated devices not seen in this many days (0 = keep all).",
    )
    cleanup_done_reminder_days = models.PositiveIntegerField(
        default=14, help_text="Delete finished server (cron) reminders — sent/expired/failed — older than this (0 = keep all).",
    )

    # ---- Telegram relay (our bot; partners send reports/messages to their own groups/chats) ----
    # Stored in the DB (not env) so the bot can be swapped from the admin Telegram page. Blank =
    # Telegram relay disabled. The username is auto-filled from getMe when the token is verified.
    telegram_bot_token = models.CharField(max_length=100, blank=True, default="")
    telegram_bot_username = models.CharField(max_length=64, blank=True, default="")
    # Our own chat/group: where server-side reports (manage.py telegram_push) are delivered.
    # Add the bot above to that group, then paste its chat id here (Telegram page → Discover chats).
    telegram_admin_chat_id = models.CharField(
        max_length=64, blank=True, default="",
        help_text="Chat id for our own reports and alerts, e.g. -1001234567890.",
    )
    # getUpdates is a queue: each call confirms everything before this id, so the next scan only
    # returns what's new. Telegram drops unconfirmed updates after ~24h, hence the cron scan.
    telegram_updates_offset = models.BigIntegerField(default=0)
    # Live mode: Telegram posts each message to /telegram/hook/<secret> instead of us polling.
    # Blank secret = polling (the push cron reads getUpdates).
    telegram_webhook_secret = models.CharField(max_length=64, blank=True, default="")
    # Rolling window: how many messages to keep per chat (0 = no cap — the age window still applies).
    telegram_keep_per_chat = models.PositiveIntegerField(
        default=50,
        help_text="Messages kept per Telegram chat. Older ones are trimmed. 0 = keep all "
                  "(the retention window below still applies).",
    )

    # ---- Live radio (AudioSync broadcasters register their public stream URL here) ----
    radio_enabled = models.BooleanField(
        default=False, help_text="Master on/off for the in-app Radio feature.",
    )
    radio_ingest_key = models.CharField(
        max_length=64, blank=True, default="",
        help_text="Shared Bearer key AudioSync uses to register/heartbeat its stream URL.",
    )
    radio_heartbeat_interval_seconds = models.PositiveIntegerField(
        default=15, help_text="How often (seconds) a broadcaster heartbeats — sent back to AudioSync.",
    )
    radio_stale_after_seconds = models.PositiveIntegerField(
        default=45, help_text="Drop a channel this many seconds after its last heartbeat (≈3 missed beats).",
    )

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "App configuration"
        verbose_name_plural = "App configuration"

    def save(self, *args, **kwargs):
        self.pk = 1  # enforce singleton
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return "App configuration"


# =============== Push notification log / composer ===============
PUSH_SOURCE_CHOICES = [
    ("admin", "Push page"),
    ("test", "Test push"),
    ("scheduled", "Scheduled"),
    ("partner_api", "Partner API"),
    ("partner_token", "Partner website"),
    ("verification", "Verification"),
    ("chat", "Chat"),
    ("other", "Not recorded"),
]

PUSH_STATUS_CHOICES = [
    ("sent", "Sent"),
    ("partial", "Partly failed"),
    ("failed", "Failed"),
    ("no_devices", "No devices"),
    ("not_configured", "FCM not configured"),
    ("error", "Error"),
]


class AppNotificationLog(models.Model):
    """One push, whichever part of SyncUp sent it. Written by fcm.push(); browsed under
    Mobile App → Push Log. Creating a row in the Django admin sends it (see admin.py).

    `account` blank = not aimed at one user: a broadcast, or a chat alert to the support agents.
    Rows from before sources were tracked have source "other" and a blank status.
    """

    account = models.ForeignKey(
        AppAccount, on_delete=models.SET_NULL, null=True, blank=True, related_name="notifications",
        help_text="Leave blank to broadcast to all active devices.",
    )
    partner = models.ForeignKey(
        AppPartner, on_delete=models.SET_NULL, null=True, blank=True, related_name="push_logs",
    )
    link = models.ForeignKey(
        AppLink, on_delete=models.SET_NULL, null=True, blank=True, related_name="push_logs",
    )
    source = models.CharField(max_length=20, choices=PUSH_SOURCE_CHOICES, default="other", db_index=True)
    # Set for Push-page broadcasts to an audience (everyone / signed_out / signed_in) sent via topics.
    audience = models.CharField(max_length=12, blank=True, default="")
    status = models.CharField(max_length=20, choices=PUSH_STATUS_CHOICES, blank=True, default="",
                              db_index=True)
    title = models.CharField(max_length=200)
    body = models.TextField()
    data = models.JSONField(null=True, blank=True)
    image_url = models.CharField(max_length=500, null=True, blank=True)
    sent_at = models.DateTimeField(auto_now_add=True, db_index=True)
    devices = models.PositiveIntegerField(default=0, help_text="Device tokens the push was addressed to.")
    success_count = models.IntegerField(default=0)
    fail_count = models.IntegerField(default=0)
    error = models.TextField(null=True, blank=True)

    class Meta:
        ordering = ["-sent_at"]

    @property
    def target_label(self):
        """Who the push was for, in words."""
        if self.audience:
            label = {"everyone": "Everyone", "signed_out": "Signed out", "signed_in": "Signed in"}.get(
                self.audience, self.audience)
            return f"{label} (topic)"
        if self.account_id:
            return self.account.login_label
        if self.source == "chat":
            return "Support agents"
        if self.partner_id:
            return f"All {self.partner.name} users"
        if self.source == "test":
            return "Selected accounts"
        return "All devices"

    @property
    def data_json(self):
        return json.dumps(self.data, indent=2, ensure_ascii=False) if self.data else ""

    def __str__(self):
        return f"{self.title} → {self.target_label}"


# =============== Telegram relay log ===============
class AppTelegramLog(models.Model):
    """One Telegram message our bot sent on a partner's behalf (or from the admin test console).
    Addressed by raw chat_id — the partner adds our bot to their group/chat and sends us the id."""

    STATUS = [("sent", "Sent"), ("failed", "Failed")]

    # Null = sent from the admin Telegram page (no partner).
    partner = models.ForeignKey(
        AppPartner, on_delete=models.SET_NULL, null=True, blank=True, related_name="telegram_logs",
    )
    chat_id = models.CharField(max_length=64)
    text = models.TextField()
    status = models.CharField(max_length=10, choices=STATUS, default="sent")
    message_id = models.CharField(max_length=40, blank=True, default="")  # Telegram's message id
    error = models.CharField(max_length=300, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["-created_at"])]

    def __str__(self):
        return f"telegram → {self.chat_id} ({self.status})"


# =============== Live radio channels (AudioSync presence registry) ===============
class RadioChannel(models.Model):
    """One live radio station: an AudioSync broadcaster's public stream URL.

    Liveness is heartbeat-driven and evaluated at READ time (no cron): a channel counts as live
    only while `is_live` AND its last heartbeat is within AppConfig.radio_stale_after_seconds.
    Keyed by a stable `broadcaster_id` so reconnects/URL-rotations update one row (no duplicates);
    `session_id` guards against stale heartbeats/goodbyes from a previous run of the same broadcaster.
    """

    broadcaster_id = models.CharField(max_length=64, unique=True, db_index=True)
    name = models.CharField(max_length=120, default="Radio")
    stream_url = models.URLField(max_length=500)
    session_id = models.CharField(max_length=64, blank=True, default="")
    is_live = models.BooleanField(default=True)
    now_playing = models.CharField(max_length=300, blank=True, default="")
    listeners = models.PositiveIntegerField(default=0)
    last_heartbeat_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def is_fresh(self, stale_after_seconds):
        """Live AND heartbeated recently enough to still be on air."""
        if not self.is_live:
            return False
        return (timezone.now() - self.last_heartbeat_at).total_seconds() <= stale_after_seconds

    def __str__(self):
        return f"{self.name} ({'live' if self.is_live else 'off'})"


# =============== Scheduled reminders (server-authored, device-fired) ===============
REMINDER_RECURRENCE_CHOICES = [
    ("once", "Once"),
    ("daily", "Everyday"),
    ("interval", "Repeat N times"),
]

# How the notification actually reaches the user.
#   device — synced to the phone (GET /app/v1/reminders) and fired by a local alarm. Exact,
#            works offline, but Doze/OEM battery killers can drop it.
#   cron   — never synced to the phone; the server sends an FCM push when an external cron
#            service calls POST /cron/push/dispatch. Reliable, but only as punctual as the
#            cron interval.
REMINDER_DELIVERY_CHOICES = [
    ("device", "Reminder — fired by the phone (local alarm)"),
    ("cron", "Push — sent by the server (cron)"),
]

# Send state. Only meaningful for delivery="cron"; device rows stay at the default.
REMINDER_STATUS_CHOICES = [
    ("pending", "Pending"),
    ("sending", "Sending"),
    ("sent", "Sent"),
    ("failed", "Failed"),
    ("expired", "Expired"),
]


class AppReminder(models.Model):
    """A notification authored in the admin, delivered one of two ways (see `delivery`).

    `account` blank = broadcast to all accounts.

    delivery="device" (default): the app syncs these (`GET /app/v1/reminders`) and schedules a
    local notification for each; tapping it opens `link` in the in-app WebView. FCM is only used
    to nudge a re-sync. See md/syncup-android-backend-plan.md §12.

    delivery="cron": excluded from the sync entirely (so the phone never double-fires it) and
    sent from the server as an FCM push by the cron dispatcher — see cron_views.dispatch_push.
    """

    account = models.ForeignKey(
        AppAccount, on_delete=models.CASCADE, null=True, blank=True, related_name="reminders",
        help_text="Leave blank to send this reminder to all accounts.",
    )
    delivery = models.CharField(
        max_length=10, choices=REMINDER_DELIVERY_CHOICES, default="device",
        help_text="Who fires it: the phone's own alarm, or the server on the next cron run.",
    )
    title = models.CharField(max_length=200)
    body = models.TextField()
    link = models.ForeignKey(
        AppLink, on_delete=models.SET_NULL, null=True, blank=True, related_name="reminders",
        help_text="Optional — tapping the reminder opens this account link in the app.",
    )
    custom_url = models.URLField(
        max_length=500, null=True, blank=True,
        help_text="Optional HTTPS URL (campaign/form) to open on tap. Overrides the link above; "
                  "works for broadcasts too.",
    )
    image_url = models.URLField(
        max_length=500, null=True, blank=True,
        help_text="Optional HTTPS image shown in the expanded notification.",
    )
    scheduled_at = models.DateTimeField(help_text="When the reminder first fires (server/IST time).")
    recurrence = models.CharField(max_length=20, choices=REMINDER_RECURRENCE_CHOICES, default="once")
    repeat_interval_seconds = models.PositiveIntegerField(
        default=0,
        help_text="For 'Repeat N times': seconds between fires. Under ~15 min is best-effort "
                  "(Android may delay it while the phone is idle).",
    )
    repeat_count = models.PositiveIntegerField(
        default=0,
        help_text="For 'Repeat N times': how many times to fire in total, then stop.",
    )
    is_active = models.BooleanField(default=True)

    # ---- Server-send state (delivery="cron" only; device rows keep the defaults) ----
    status = models.CharField(max_length=10, choices=REMINDER_STATUS_CHOICES, default="pending")
    claimed_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When a cron run claimed this row — used to reap sends that died mid-flight.",
    )
    sent_at = models.DateTimeField(null=True, blank=True, help_text="When the push actually went out.")
    success_count = models.IntegerField(default=0)
    fail_count = models.IntegerField(default=0)
    attempts = models.PositiveIntegerField(default=0, help_text="Dispatch attempts, including retries.")
    fires_done = models.PositiveIntegerField(default=0, help_text="Sends completed so far (Repeat N times).")
    last_error = models.TextField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-scheduled_at"]
        indexes = [
            models.Index(fields=["account", "is_active"]),
            # The cron dispatcher's due-query, run on every tick.
            models.Index(fields=["delivery", "status", "scheduled_at"]),
        ]

    def next_occurrence(self, after):
        """The next fire time strictly after `after`, or None if this was the last one.

        Used by the cron dispatcher to re-arm a recurring push once it has been sent, and to
        skip past occurrences missed while the cron was down.
        """
        if self.recurrence == "daily":
            step = timedelta(days=1)
        elif self.recurrence == "interval" and self.repeat_interval_seconds > 0:
            if self.repeat_count and self.fires_done >= self.repeat_count:
                return None
            step = timedelta(seconds=self.repeat_interval_seconds)
        else:  # "once", or an interval row with no usable interval
            return None
        nxt = self.scheduled_at
        while nxt <= after:
            nxt += step
        return nxt

    def __str__(self):
        target = self.account.login_label if self.account else "all accounts"
        return f"{self.title} → {target} @ {self.scheduled_at:%Y-%m-%d %H:%M}"


class AppReminderReceipt(models.Model):
    """Per-device delivery receipt for a reminder, reported back by the app.

    `synced_at` = the device downloaded/scheduled the reminder; `fired_at` = the device actually
    showed the notification. One row per (reminder, account, device), updated in place — so for a
    recurring reminder `fired_at` holds the LAST time it was shown.
    """

    reminder = models.ForeignKey(AppReminder, on_delete=models.CASCADE, related_name="receipts")
    account = models.ForeignKey(AppAccount, on_delete=models.CASCADE, null=True, related_name="reminder_receipts")
    device_id = models.CharField(max_length=255)
    synced_at = models.DateTimeField(null=True, blank=True)
    fired_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["reminder", "account", "device_id"], name="unique_reminder_receipt",
            ),
        ]

    def __str__(self):
        return f"{self.reminder_id} · {self.device_id[:8]}…"


# =============== Cron job lock ===============
class CronLock(models.Model):
    """A named, self-expiring lock so two overlapping cron calls can't run the same job.

    Deliberately DB-backed rather than cache-backed: there's no shared CACHES backend configured,
    so Django falls back to per-process LocMemCache — a second worker would see an empty cache and
    take the lock anyway. `select_for_update()` is no help either (a no-op on SQLite), so the
    lock is taken with a conditional UPDATE, which *is* atomic on SQLite.
    """

    name = models.CharField(max_length=64, unique=True)
    locked_until = models.DateTimeField()
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def acquire(cls, name, ttl_seconds):
        """Take the lock, stealing it if the previous holder's TTL has lapsed. True if we got it."""
        now = timezone.now()
        until = now + timedelta(seconds=ttl_seconds)
        # Conditional UPDATE: succeeds only if the row exists AND is free/expired.
        if cls.objects.filter(name=name, locked_until__lt=now).update(locked_until=until):
            return True
        if cls.objects.filter(name=name).exists():
            return False  # held by a run that's still within its TTL
        try:
            cls.objects.create(name=name, locked_until=until)
            return True
        except IntegrityError:
            return False  # another worker created it a moment ago

    @classmethod
    def release(cls, name):
        cls.objects.filter(name=name).update(locked_until=timezone.now())

    def __str__(self):
        return f"{self.name} → {self.locked_until:%Y-%m-%d %H:%M:%S}"


CHAT_SENDER_CHOICES = [
    ("user", "User"),
    ("admin", "Admin"),
]


class AppChatMessage(models.Model):
    """One message in the private user↔admin chat thread.

    Each account has a single conversation with "the admin" (support). `sender` says who wrote it.
    The app polls/sends via a web chat page; the admin reads and replies from /mobile/chats.
    """

    account = models.ForeignKey(
        AppAccount, on_delete=models.CASCADE, related_name="chat_messages",
    )
    sender = models.CharField(max_length=10, choices=CHAT_SENDER_CHOICES)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    read_by_user = models.BooleanField(default=False)
    read_by_admin = models.BooleanField(default=False)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["account", "created_at"]),
        ]

    def __str__(self):
        return f"{self.account.login_label} · {self.sender} · {self.body[:24]}"


# =============== Browser sync (a signed-in user's own Normal-section data) ===============
SYNC_KINDS = [
    ("bookmark", "Bookmark"),
    ("history", "History"),
    ("shortcut", "Home shortcut"),
    ("setting", "Setting"),
    ("tabs", "Open tabs (one row per device)"),
]


class SyncItem(models.Model):
    """One synced browser item. Keyed by (account, kind, key) where `key` is the app's own stable id
    (a UUID for bookmarks/history/shortcuts, the setting name, or the device id for 'tabs').

    The latest change wins: an incoming change applies only if its `updated_ms` (the phone's clock
    when the user made the change) is newer. Deletions are kept as tombstones (`deleted`) so they
    reach every device; `changed_at` is the server time used as the sync cursor."""

    account = models.ForeignKey(AppAccount, on_delete=models.CASCADE, related_name="sync_items")
    kind = models.CharField(max_length=10, choices=SYNC_KINDS)
    key = models.CharField(max_length=128)
    data = models.JSONField(default=dict, blank=True)
    updated_ms = models.BigIntegerField(default=0)
    deleted = models.BooleanField(default=False)
    device_id = models.CharField(max_length=255, blank=True, default="")  # last writer
    changed_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        ordering = ["changed_at"]
        constraints = [
            models.UniqueConstraint(fields=["account", "kind", "key"], name="uniq_sync_item"),
        ]
        indexes = [models.Index(fields=["account", "changed_at"])]

    def __str__(self):
        return f"{self.account_id} · {self.kind} · {self.key[:12]}"
