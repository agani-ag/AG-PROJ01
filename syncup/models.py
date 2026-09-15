import hashlib
import re
import secrets
from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone
from django.utils.timezone import now


# Not used by any current model, but the initial migration references it as a field default,
# so it has to stay importable or the migration history won't load.
def default_future_date():
    return now().date() + timedelta(days=15)


# =============== Admin sign-in (PIN + magic link) ===============
# AG-PROJ01 is admin-only. The admin's name is User.first_name and the password lives on User;
# these two tables hold the admin's other two ways in.
class AdminPin(models.Model):
    """The admin's 5-character PIN, used by "Forgot Password?" on the login page.

    Kept as a salted hash, like a password, so a PIN login checks each admin's hash rather than
    looking the PIN up directly.
    """
    PATTERN = re.compile(r'[A-Z0-9]{5}')

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='admin_pin')
    pin_hash = models.CharField(max_length=128)
    updated_at = models.DateTimeField(auto_now=True)

    def set_pin(self, raw_pin):
        self.pin_hash = make_password(raw_pin.upper())

    def check_pin(self, raw_pin):
        return check_password((raw_pin or '').upper(), self.pin_hash)

    def __str__(self):
        return f"PIN for {self.user.username}"


class AdminLoginLink(models.Model):
    """A one-time magic sign-in link. Only a SHA-256 of the token is stored — the token itself
    appears once, on the Profile page, inside the link."""
    TTL = timedelta(minutes=15)

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='admin_login_links')
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    @staticmethod
    def hash_token(token):
        return hashlib.sha256((token or '').encode()).hexdigest()

    @classmethod
    def issue(cls, user):
        """Create a fresh link for `user`, cancelling any earlier ones. Returns the raw token."""
        cls.objects.filter(user=user).delete()
        token = secrets.token_urlsafe(32)
        cls.objects.create(user=user, token_hash=cls.hash_token(token),
                           expires_at=timezone.now() + cls.TTL)
        return token

    @classmethod
    def _live(cls, token):
        link = cls.objects.select_related('user').filter(
            token_hash=cls.hash_token(token), used_at__isnull=True, expires_at__gt=timezone.now(),
        ).first()
        if link and link.user.is_active and link.user.is_superuser:
            return link
        return None

    @classmethod
    def is_valid(cls, token):
        """True if `token` would sign someone in right now. Doesn't use it up."""
        return cls._live(token) is not None

    @classmethod
    def redeem(cls, token):
        """Use `token` up and return its user, or None if it's unknown, expired, already used, or
        its user is no longer an active superuser. The conditional UPDATE is what makes it single
        use: select_for_update() is a no-op on SQLite, so this is what stops two tabs both winning.
        """
        link = cls._live(token)
        if link is None:
            return None
        if cls.objects.filter(pk=link.pk, used_at__isnull=True).update(used_at=timezone.now()) != 1:
            return None
        return link.user

    def __str__(self):
        return f"Login link for {self.user.username} (expires {self.expires_at:%Y-%m-%d %H:%M})"


# =============== Live Channels (Radio / TV) ===============
class LiveChannel(models.Model):
    """A broadcast channel (Radio = audio, TV = video).

    Sync model = "computed schedule": the server stores only the ordered
    playlist + a single `anchor_time` (when the broadcast started) + a `version`.
    Every viewer's browser computes what's playing right now from the clock,
    so there is no server-side job and nothing streams through the server.
    """
    KIND_CHOICES = (('audio', 'Radio (audio)'), ('video', 'TV (video)'))

    slug = models.SlugField(max_length=20, unique=True)      # 'radio' | 'tv'
    name = models.CharField(max_length=60)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    is_live = models.BooleanField(default=False)
    loop = models.BooleanField(default=True)                 # 24/7 loop
    anchor_time = models.DateTimeField(default=now)          # broadcast start instant
    version = models.PositiveIntegerField(default=1)         # bump to force viewers to resync
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} ({self.slug})"

class LiveTrack(models.Model):
    SOURCE_CHOICES = (
        ('youtube', 'YouTube'), ('vimeo', 'Vimeo'),
        ('file', 'File'), ('hls', 'HLS'),
    )
    channel = models.ForeignKey(LiveChannel, on_delete=models.CASCADE, related_name='tracks')
    order = models.PositiveIntegerField(default=0)
    title = models.CharField(max_length=200, blank=True)
    url = models.URLField(max_length=500)
    source_type = models.CharField(max_length=10, choices=SOURCE_CHOICES, default='youtube')
    duration_seconds = models.PositiveIntegerField(default=0)  # required for the schedule
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order', 'id']

    def __str__(self):
        return f"{self.channel.slug} #{self.order} {self.title or self.url}"

# =============== Live Broadcast (Mode 3 — WebRTC) ===============
class BroadcastState(models.Model):
    """Single-row on-air flag for the live broadcast, with an admin heartbeat.

    `is_live` + a fresh `updated_at` (heartbeat) together mean 'on air'. If the
    admin tab closes, the heartbeat goes stale and listeners treat it as off-air.
    """
    room = models.SlugField(max_length=20, unique=True, default='live')
    is_live = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Broadcast {self.room} ({'live' if self.is_live else 'off'})"

class SignalMessage(models.Model):
    """Short-lived WebRTC signaling relay (SDP offers/answers + ICE candidates).

    A tiny mailbox: each side posts messages addressed `to_peer` and polls for
    messages addressed to itself. Rows are auto-purged after ~60s.
    """
    room = models.SlugField(max_length=20, default='live')
    from_peer = models.CharField(max_length=64)
    to_peer = models.CharField(max_length=64)
    kind = models.CharField(max_length=20)      # offer | answer | candidate | bye
    data = models.TextField(blank=True)         # JSON string
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['room', 'to_peer', 'id'])]