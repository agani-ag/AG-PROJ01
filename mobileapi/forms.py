"""Forms for the HTML admin screens (Bootstrap-styled, matching the syncup app)."""
import json
import re
from datetime import timezone as dt_timezone

from django import forms
from django.utils import timezone

from .models import AppAccount, AppConfig, AppLink, AppReminder


def _bootstrap(fields, checkbox_fields=()):
    for name, field in fields.items():
        if name in checkbox_fields:
            field.widget.attrs.update({"class": "form-check-input"})
        else:
            css = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = (css + " form-control").strip()


class AppAccountForm(forms.ModelForm):
    new_password = forms.CharField(
        label="Password",
        required=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="Set a password (required for a new account). Leave blank to keep the current one.",
    )

    class Meta:
        model = AppAccount
        fields = ["name", "email", "is_active", "admin_chat_mode", "can_manage_links", "show_general_links"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrap(self.fields, checkbox_fields=(
            "is_active", "admin_chat_mode", "can_manage_links", "show_general_links",
        ))

    def clean_new_password(self):
        pw = self.cleaned_data.get("new_password")
        if not self.instance.pk and not pw:
            raise forms.ValidationError("A password is required for a new account.")
        if pw and len(pw) < 6:
            raise forms.ValidationError("Password must be at least 6 characters.")
        return pw

    def save(self, commit=True):
        account = super().save(commit=False)
        pw = self.cleaned_data.get("new_password")
        if pw:
            account.set_password(pw)
        if commit:
            account.save()
        return account


class AppLinkForm(forms.ModelForm):
    class Meta:
        model = AppLink
        fields = ["title", "url", "description", "icon", "is_active", "notify_token_enabled"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrap(self.fields, checkbox_fields=("is_active", "notify_token_enabled"))
        # Icon is a choice field → use a Bootstrap select.
        self.fields["icon"].widget.attrs["class"] = "form-select"
        self.fields["icon"].required = False


class GeneralLinkForm(forms.ModelForm):
    """A shared link with no account — shown to every user (who has general links enabled).
    No partner notify-token here: that's per-user and meaningless for a general link."""

    class Meta:
        model = AppLink
        fields = ["title", "url", "description", "icon", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrap(self.fields, checkbox_fields=("is_active",))
        self.fields["icon"].widget.attrs["class"] = "form-select"
        self.fields["icon"].required = False


class AppConfigForm(forms.ModelForm):
    class Meta:
        model = AppConfig
        fields = [
            "min_supported_version",
            "support_email",
            "support_phone",
            "privacy_company_name",
            "privacy_effective_date",
            "privacy_contact_email",
            "announcement_active",
            "announcement_title",
            "announcement_message",
            "announcement_fullscreen",
            "announcement_blocking",
            "cron_dispatch_interval_minutes",
            "cleanup_log_days",
            "cleanup_chat_days",
            "cleanup_inactive_device_days",
            "cleanup_done_reminder_days",
            "feature_flags",
        ]
        widgets = {
            "announcement_message": forms.Textarea(attrs={"rows": 3}),
            "feature_flags": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrap(
            self.fields,
            checkbox_fields=("announcement_active", "announcement_fullscreen", "announcement_blocking"),
        )


# Advanced FCM (AndroidConfig) options exposed on the Push form. Blank/"—" = leave unset.
FCM_PRIORITY_CHOICES = [("", "— default —"), ("high", "High (wake / heads-up)"), ("normal", "Normal (battery-friendly)")]
FCM_SOUND_CHOICES = [("", "— default —"), ("default", "Play default sound"), ("silent", "Silent")]
FCM_NOTIF_PRIORITY_CHOICES = [("", "— default —"), ("min", "Min"), ("low", "Low"), ("default", "Default"), ("high", "High"), ("max", "Max")]
FCM_VISIBILITY_CHOICES = [("", "— default —"), ("private", "Private (hide on lock screen)"), ("public", "Public"), ("secret", "Secret")]

# Advanced-option keys whose form field "fcm_<key>" maps straight to the same key in the dict
# fcm.build_android_config expects. (ttl, raw and event_time are handled specially below.)
FCM_OPTION_KEYS = (
    "priority", "sound", "color", "notification_priority", "visibility", "collapse_tag",
    "sticky", "local_only", "notification_count", "ticker", "analytics_label",
    "restrict_package", "vibrate", "led_color", "led_on_ms", "led_off_ms",
)


class PushForm(forms.Form):
    account = forms.ModelChoiceField(
        queryset=AppAccount.objects.filter(is_active=True),
        required=False,
        empty_label="All devices (broadcast)",
    )
    title = forms.CharField(max_length=200)
    body = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))
    link_url = forms.CharField(
        required=False, max_length=500, label="Open URL on tap",
        help_text="Optional HTTPS URL (campaign/form) opened in the app on tap.",
    )
    image_url = forms.CharField(
        required=False, max_length=500, label="Image URL",
        help_text="Optional HTTPS image shown in the expanded notification (≈2:1, e.g. 1024×512).",
    )

    # ---- Advanced FCM options (apply to backgrounded / system-rendered pushes) ----
    fcm_priority = forms.ChoiceField(choices=FCM_PRIORITY_CHOICES, required=False, label="Delivery priority")
    fcm_sound = forms.ChoiceField(choices=FCM_SOUND_CHOICES, required=False, label="Sound")
    fcm_color = forms.CharField(required=False, max_length=7, label="Accent color", widget=forms.TextInput(attrs={"type": "color"}))
    fcm_notification_priority = forms.ChoiceField(choices=FCM_NOTIF_PRIORITY_CHOICES, required=False, label="Importance")
    fcm_visibility = forms.ChoiceField(choices=FCM_VISIBILITY_CHOICES, required=False, label="Lock-screen visibility")
    fcm_collapse_tag = forms.CharField(
        required=False, max_length=64, label="Collapse tag",
        help_text="A newer push with the same tag replaces the older one instead of stacking.",
    )
    fcm_ttl_hours = forms.IntegerField(
        required=False, min_value=0, max_value=672, label="TTL (hours)",
        help_text="How long FCM keeps trying if the device is offline (max 28 days). Blank = default.",
    )
    # ---- Group A extras ----
    fcm_sticky = forms.BooleanField(required=False, label="Ongoing / sticky (can't be swiped away)")
    fcm_local_only = forms.BooleanField(required=False, label="Local only (don't mirror to wearables)")
    fcm_restrict_package = forms.BooleanField(required=False, label="Deliver only to the SyncUp app")
    fcm_notification_count = forms.IntegerField(
        required=False, min_value=0, label="Badge count", help_text="Number on the app-icon badge.",
    )
    fcm_ticker = forms.CharField(required=False, max_length=200, label="Ticker text")
    fcm_analytics_label = forms.CharField(
        required=False, max_length=50, label="Analytics label",
        help_text="Groups this send in Firebase delivery reports.",
    )
    fcm_event_time = forms.DateTimeField(
        required=False, label="Event time",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"],
        help_text="Optional timestamp shown on the notification (interpreted as IST). Per-send only.",
    )
    fcm_vibrate = forms.CharField(
        required=False, max_length=100, label="Vibration pattern",
        help_text="Comma-separated seconds, e.g. 0.25, 0.25, 0.5 (channel-governed on Android 8+).",
    )
    fcm_led_color = forms.CharField(
        required=False, max_length=7, label="LED color", widget=forms.TextInput(attrs={"type": "color"}),
    )
    fcm_led_on_ms = forms.IntegerField(required=False, min_value=0, label="LED on (ms)")
    fcm_led_off_ms = forms.IntegerField(required=False, min_value=0, label="LED off (ms)")
    fcm_raw_json = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 3}), label="Raw android overrides (JSON)",
        help_text='Any other FCM AndroidConfig field, merged last. e.g. {"notification":{"sticky":true}}',
    )
    fcm_save_defaults = forms.BooleanField(
        required=False, label="Save these as master defaults for future pushes",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrap(self.fields)
        for name in ("account", "fcm_priority", "fcm_sound", "fcm_notification_priority", "fcm_visibility"):
            self.fields[name].widget.attrs["class"] = "form-select"
        for name in ("fcm_save_defaults", "fcm_sticky", "fcm_local_only", "fcm_restrict_package"):
            self.fields[name].widget.attrs["class"] = "form-check-input"
        # In-place hints (skip color/select/checkbox/datetime widgets — they ignore placeholders).
        placeholders = {
            "title": "e.g. Weekend offer is live",
            "body": "The message users will read…",
            "link_url": "https://example.com/campaign",
            "image_url": "https://example.com/banner-1024x512.jpg",
            "fcm_collapse_tag": "e.g. promo-aug (newer replaces older)",
            "fcm_ttl_hours": "e.g. 24  (blank = default)",
            "fcm_notification_count": "e.g. 3",
            "fcm_ticker": "e.g. New offer available",
            "fcm_analytics_label": "e.g. aug-campaign",
            "fcm_vibrate": "e.g. 0.25, 0.25, 0.5",
            "fcm_led_on_ms": "e.g. 500",
            "fcm_led_off_ms": "e.g. 800",
            "fcm_raw_json": '{"notification": {"proxy": "ALLOW"}}',
        }
        for name, hint in placeholders.items():
            if name in self.fields:
                self.fields[name].widget.attrs.setdefault("placeholder", hint)
        # Pre-fill the advanced fields from the saved master defaults (unbound forms only).
        # event_time is per-send only, so it is never persisted or pre-filled.
        if not self.is_bound:
            defaults = AppConfig.load().fcm_push_defaults or {}
            for key in FCM_OPTION_KEYS:
                if defaults.get(key) not in (None, "", [], False):
                    self.fields[f"fcm_{key}"].initial = defaults[key]
            if defaults.get("ttl_seconds"):
                self.fields["fcm_ttl_hours"].initial = int(defaults["ttl_seconds"]) // 3600
            if defaults.get("raw"):
                self.fields["fcm_raw_json"].initial = json.dumps(defaults["raw"])

    def _clean_https(self, field):
        url = (self.cleaned_data.get(field) or "").strip()
        if url and not url.lower().startswith("https://"):
            raise forms.ValidationError("URL must be HTTPS (the in-app browser blocks non-secure URLs).")
        return url

    def clean_link_url(self):
        return self._clean_https("link_url")

    def clean_image_url(self):
        return self._clean_https("image_url")

    def _clean_hex(self, field):
        color = (self.cleaned_data.get(field) or "").strip()
        if color and not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
            raise forms.ValidationError("Enter a hex color like #RRGGBB.")
        return color

    def clean_fcm_color(self):
        return self._clean_hex("fcm_color")

    def clean_fcm_led_color(self):
        return self._clean_hex("fcm_led_color")

    def clean_fcm_raw_json(self):
        raw = (self.cleaned_data.get("fcm_raw_json") or "").strip()
        if not raw:
            return ""
        try:
            parsed = json.loads(raw)
        except ValueError as e:
            raise forms.ValidationError(f"Invalid JSON: {e}")
        if not isinstance(parsed, dict):
            raise forms.ValidationError("Raw overrides must be a JSON object, e.g. {\"collapse_key\":\"x\"}.")
        return raw

    def fcm_options(self):
        """Collected advanced options as the dict fcm.build_android_config expects (post-clean).

        `persistable=False` on the returned nothing — callers persist this same dict as the master
        defaults; event_time is deliberately excluded from persistence (it's a one-off timestamp).
        """
        cd = self.cleaned_data
        opts = {}
        for key in FCM_OPTION_KEYS:
            val = cd.get(f"fcm_{key}")
            if val not in (None, "", [], False):
                opts[key] = val
        if cd.get("fcm_ttl_hours") not in (None, ""):
            opts["ttl_seconds"] = int(cd["fcm_ttl_hours"]) * 3600
        if cd.get("fcm_raw_json"):
            opts["raw"] = json.loads(cd["fcm_raw_json"])
        # event_time: per-send only. Interpret the naive picker value as server tz → RFC3339 UTC.
        et = cd.get("fcm_event_time")
        if et:
            if timezone.is_naive(et):
                et = timezone.make_aware(et, timezone.get_current_timezone())
            opts["event_time"] = et.astimezone(dt_timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return opts


# "Repeat N times" interval, entered as a value + unit in the admin and stored as seconds.
# Minimum enforced below (MIN_INTERVAL_SECONDS) — Android throttles anything shorter when idle.
REMINDER_INTERVAL_UNITS = [
    ("60", "Minutes"),
    ("3600", "Hours"),
    ("86400", "Days"),
    ("604800", "Weeks"),
]

# Shortest interval we allow for a real reminder (15 minutes). Below this, Doze delays/bunches fires.
MIN_INTERVAL_SECONDS = 15 * 60


class ReminderForm(forms.ModelForm):
    # Not model fields — combined into repeat_interval_seconds on save.
    interval_value = forms.IntegerField(
        required=False, min_value=1, label="Repeat every",
    )
    interval_unit = forms.ChoiceField(
        required=False, choices=REMINDER_INTERVAL_UNITS, initial="60",
    )

    class Meta:
        model = AppReminder
        fields = ["account", "delivery", "title", "body", "custom_url",
                  "image_url", "scheduled_at", "recurrence",
                  "repeat_count", "is_active"]
        widgets = {
            "body": forms.Textarea(attrs={"rows": 3}),
            "scheduled_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M",
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].required = False
        self.fields["account"].empty_label = "All accounts (broadcast)"
        # datetime-local sends "YYYY-MM-DDTHH:MM"; accept a couple of extra shapes too.
        self.fields["scheduled_at"].input_formats = [
            "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
        ]
        # Show the stored (UTC) time as local (IST) in the picker.
        if self.instance and self.instance.pk and self.instance.scheduled_at:
            self.initial["scheduled_at"] = timezone.localtime(self.instance.scheduled_at)
        # "Repeat N times" fields only apply when recurrence == interval.
        self.fields["repeat_count"].label = "Number of times"
        self.fields["repeat_count"].required = False
        # When editing an interval reminder, show its seconds as the largest whole unit.
        secs = getattr(self.instance, "repeat_interval_seconds", 0) or 0
        if secs > 0:
            for mult, _ in reversed(REMINDER_INTERVAL_UNITS):  # weeks → seconds
                m = int(mult)
                if secs % m == 0:
                    self.initial.setdefault("interval_value", secs // m)
                    self.initial.setdefault("interval_unit", mult)
                    break
        _bootstrap(self.fields, checkbox_fields=("is_active",))
        for name in ("account", "delivery", "recurrence", "interval_unit"):
            self.fields[name].widget.attrs["class"] = "form-select"

    def clean(self):
        cleaned = super().clean()
        seconds = 0
        is_cron = cleaned.get("delivery") == "cron"
        if cleaned.get("recurrence") == "interval":
            value = cleaned.get("interval_value")
            unit = cleaned.get("interval_unit")
            if not value or value < 1:
                self.add_error("interval_value", "Enter how often it repeats (1 or more).")
            if not unit:
                self.add_error("interval_unit", "Choose a unit.")
            if not cleaned.get("repeat_count") or cleaned.get("repeat_count") < 1:
                self.add_error("repeat_count", "Enter how many times it should fire (1 or more).")
            if value and unit:
                seconds = int(value) * int(unit)
                # A server-sent push can never repeat faster than the cron ticks (read live from
                # the admin-editable config); a device alarm can, but Doze bunches under ~15 min.
                cron_minutes = AppConfig.load().cron_dispatch_interval_minutes
                floor = cron_minutes * 60 if is_cron else MIN_INTERVAL_SECONDS
                if seconds < floor:
                    self.add_error(
                        "interval_value",
                        (
                            f"With server (cron) delivery the shortest repeat is "
                            f"{cron_minutes} minutes — the cron only runs that often."
                            if is_cron else
                            "Minimum repeat interval is 15 minutes "
                            "(shorter intervals aren't delivered reliably when the phone is idle)."
                        ),
                    )
        else:
            # Keep these clean for once/daily so they don't leak into the app payload.
            cleaned["repeat_count"] = 0
        self._interval_seconds = seconds
        return cleaned

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.repeat_interval_seconds = getattr(self, "_interval_seconds", 0)
        # Re-queue a server push whose time was moved (or that was just switched to cron
        # delivery) — otherwise an already-"sent" row sits there and never goes out again.
        if obj.delivery == "cron" and (
            "scheduled_at" in self.changed_data or "delivery" in self.changed_data
        ):
            obj.status = "pending"
            obj.claimed_at = None
            obj.last_error = None
        if commit:
            obj.save()
        return obj

    def clean_scheduled_at(self):
        dt = self.cleaned_data.get("scheduled_at")
        # The picker is naive; interpret it in the server timezone (IST) before saving.
        if dt and timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_current_timezone())
        return dt

    def clean_custom_url(self):
        url = (self.cleaned_data.get("custom_url") or "").strip()
        if url and not url.lower().startswith("https://"):
            raise forms.ValidationError("Custom URL must be HTTPS (the in-app browser blocks non-secure URLs).")
        return url
