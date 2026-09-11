"""
Data models for the standalone SyncUp mobile API (`com.agani.syncup`).

Fully isolated from the existing `syncup` app: no foreign keys to any existing
table, its own account/credential system (not Django `User`), its own token auth.
See md/syncup-android-backend-plan.md.
"""
import hashlib
import secrets
from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.core.validators import MinValueValidator
from django.db import IntegrityError, models
from django.utils import timezone

# How long an issued bearer token stays valid. After this the app gets a 401 and
# transparently returns the user to the login screen (see the app's 401 handling).
TOKEN_TTL_DAYS = 60


# =============== App account (mobile-only credentials) ===============
class AppAccount(models.Model):
    """A mobile app user. Separate from Django `User` (which is admin-only)."""

    name = models.CharField(max_length=100)
    email = models.EmailField(unique=True)
    password = models.CharField(max_length=128)  # PBKDF2 hash, never plaintext
    is_active = models.BooleanField(default=True)
    # Support agent: when on, this user's in-app Chat opens the full support inbox (all users'
    # conversations, excluding their own) and their replies appear to end users as "Admin".
    admin_chat_mode = models.BooleanField(
        default=False,
        help_text="Support agent — their app Chat opens the admin inbox instead of a personal chat.",
    )
    can_manage_links = models.BooleanField(
        default=False,
        help_text="Lets the user add/remove their own links from the app (only links they added).",
    )
    show_general_links = models.BooleanField(
        default=True,
        help_text="Include the shared 'general' links in this user's list. Turn off for single-link "
                  "(kiosk) users so their one link still auto-opens.",
    )
    # The partner that provisioned this user via the Partner API. Blank = admin-created. Every
    # Partner-API request is scoped to its own accounts through this FK.
    partner = models.ForeignKey(
        "AppPartner", on_delete=models.SET_NULL, null=True, blank=True, related_name="accounts",
    )
    # The partner's OWN id for this user (their system's key). Unique per partner, so a partner
    # can address / upsert / sync users by their own id without ever storing our internal id.
    external_id = models.CharField(max_length=128, null=True, blank=True)
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
        constraints = [
            # A partner's external_id is unique within that partner (nulls — admin users — ignored).
            models.UniqueConstraint(
                fields=["partner", "external_id"],
                condition=models.Q(external_id__isnull=False),
                name="uniq_partner_external_id",
            ),
        ]

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
        if self.email:
            self.email = self.email.strip().lower()
        if self.name:
            self.name = self.name.strip()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} <{self.email}>"


# =============== Bearer token ===============
class AppAuthToken(models.Model):
    """Opaque bearer token issued at login; sent as `Authorization: Bearer <key>`."""

    key = models.CharField(max_length=40, unique=True, db_index=True)
    account = models.ForeignKey(AppAccount, on_delete=models.CASCADE, related_name="tokens")
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked = models.BooleanField(default=False)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    @classmethod
    def issue(cls, account, ttl_days=TOKEN_TTL_DAYS):
        return cls.objects.create(
            key=secrets.token_hex(20),
            account=account,
            expires_at=timezone.now() + timedelta(days=ttl_days),
        )

    @property
    def is_valid(self):
        if self.revoked:
            return False
        if self.expires_at and self.expires_at < timezone.now():
            return False
        return True

    def __str__(self):
        return f"{self.account.email} · {self.key[:8]}…"


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
        return f"{self.action_type} → {self.account.email} ({self.status})"


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
    # True when the end user added it themselves (self-manage) — only these are user-removable.
    created_by_user = models.BooleanField(default=False)
    # When on, the app injects window.SyncUp={token} into this page so the site can push
    # notifications to this exact user (via POST /app/v1/partner/notify). Uncheck to revoke.
    notify_token_enabled = models.BooleanField(
        default=False,
        help_text="Inject a SyncUp notification token (window.SyncUp.token) so this site can push to this user.",
    )
    # When on, the app keeps the screen awake on this page so its audio keeps playing (radio/music).
    # Pairs with the app's dim "Radio mode" (black + low brightness) to limit battery drain.
    keep_screen_on = models.BooleanField(
        default=False,
        help_text="Keep the screen awake on this page so its audio keeps playing (for radio/music links).",
    )
    # A partner's own key for this link (their system's id). Unique per account, so a partner can
    # replace-by-key: upserting a user's link with the same key updates it instead of duplicating.
    external_id = models.CharField(max_length=128, null=True, blank=True)
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
        return f"{self.title} ({self.account.email})"


# =============== FCM device registration ===============
class AppDevice(models.Model):
    account = models.ForeignKey(AppAccount, on_delete=models.CASCADE, related_name="devices")
    device_id = models.CharField(max_length=255)
    fcm_token = models.TextField()
    platform = models.CharField(max_length=50, default="android")
    app_version = models.CharField(max_length=50, null=True, blank=True)
    last_seen = models.DateTimeField(default=timezone.now)
    # When this device last pulled the reminder list (its background/foreground reminder sync).
    last_reminder_sync_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-last_seen"]
        constraints = [
            models.UniqueConstraint(fields=["account", "device_id"], name="unique_account_device"),
        ]

    def __str__(self):
        return f"{self.account.email} · {self.device_id}"


# =============== Server-driven config (singleton) ===============
class AppConfig(models.Model):
    """Single-row config served by GET /app/v1/config (admin-managed)."""

    min_supported_version = models.IntegerField(default=1)
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
class AppNotificationLog(models.Model):
    """One push send. `account` blank = broadcast to all active devices.

    Creating a row in the admin triggers the actual FCM send (see admin.py).
    """

    account = models.ForeignKey(
        AppAccount, on_delete=models.SET_NULL, null=True, blank=True, related_name="notifications",
        help_text="Leave blank to broadcast to all active devices.",
    )
    title = models.CharField(max_length=200)
    body = models.TextField()
    data = models.JSONField(null=True, blank=True)
    sent_at = models.DateTimeField(auto_now_add=True)
    success_count = models.IntegerField(default=0)
    fail_count = models.IntegerField(default=0)

    class Meta:
        ordering = ["-sent_at"]

    def __str__(self):
        target = self.account.email if self.account else "all devices"
        return f"{self.title} → {target}"


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
        target = self.account.email if self.account else "all accounts"
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
        return f"{self.account.email} · {self.sender} · {self.body[:24]}"
