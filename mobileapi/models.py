"""
Data models for the standalone SyncUp mobile API (`com.agani.syncup`).

Fully isolated from the existing `syncup` app: no foreign keys to any existing
table, its own account/credential system (not Django `User`), its own token auth.
See md/syncup-android-backend-plan.md.
"""
import secrets
from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.db import models
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
    last_login = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def set_password(self, raw_password):
        self.password = make_password(raw_password)

    def check_password(self, raw_password):
        return check_password(raw_password, self.password)

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
    account = models.ForeignKey(AppAccount, on_delete=models.CASCADE, related_name="links")
    title = models.CharField(max_length=100)
    url = models.URLField(max_length=500)
    description = models.CharField(max_length=200, null=True, blank=True)
    icon = models.CharField(max_length=50, null=True, blank=True, choices=APP_ICON_CHOICES)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["title"]  # links always shown ascending by title
        indexes = [
            models.Index(fields=["account", "is_active"]),  # speeds the per-account active-link query
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


class AppReminder(models.Model):
    """A reminder authored in the admin and fired ON-DEVICE via a local alarm.

    `account` blank = broadcast to all accounts. The app syncs these
    (`GET /app/v1/reminders`) and schedules a local notification for each; tapping it
    opens `link` in the in-app WebView. FCM is only used to nudge a re-sync.
    See md/syncup-android-backend-plan.md §12.
    """

    account = models.ForeignKey(
        AppAccount, on_delete=models.CASCADE, null=True, blank=True, related_name="reminders",
        help_text="Leave blank to send this reminder to all accounts.",
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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-scheduled_at"]
        indexes = [
            models.Index(fields=["account", "is_active"]),
        ]

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
