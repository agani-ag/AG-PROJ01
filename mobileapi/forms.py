"""Forms for the HTML admin screens (Bootstrap-styled, matching the syncup app)."""
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
        fields = ["name", "email", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrap(self.fields, checkbox_fields=("is_active",))

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
        fields = ["title", "url", "description", "icon", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrap(self.fields, checkbox_fields=("is_active",))
        # Icon is a choice field → use a Bootstrap select.
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrap(self.fields)

    def _clean_https(self, field):
        url = (self.cleaned_data.get(field) or "").strip()
        if url and not url.lower().startswith("https://"):
            raise forms.ValidationError("URL must be HTTPS (the in-app browser blocks non-secure URLs).")
        return url

    def clean_link_url(self):
        return self._clean_https("link_url")

    def clean_image_url(self):
        return self._clean_https("image_url")


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
        fields = ["account", "title", "body", "custom_url",
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
        for name in ("account", "recurrence", "interval_unit"):
            self.fields[name].widget.attrs["class"] = "form-select"

    def clean(self):
        cleaned = super().clean()
        seconds = 0
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
                if seconds < MIN_INTERVAL_SECONDS:
                    self.add_error(
                        "interval_value",
                        "Minimum repeat interval is 15 minutes "
                        "(shorter intervals aren't delivered reliably when the phone is idle).",
                    )
        else:
            # Keep these clean for once/daily so they don't leak into the app payload.
            cleaned["repeat_count"] = 0
        self._interval_seconds = seconds
        return cleaned

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.repeat_interval_seconds = getattr(self, "_interval_seconds", 0)
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
