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
            "latest_version",
            "support_email",
            "support_phone",
            "announcement_active",
            "announcement_title",
            "announcement_message",
            "feature_flags",
        ]
        widgets = {
            "announcement_message": forms.Textarea(attrs={"rows": 3}),
            "feature_flags": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrap(self.fields, checkbox_fields=("announcement_active",))


class PushForm(forms.Form):
    account = forms.ModelChoiceField(
        queryset=AppAccount.objects.filter(is_active=True),
        required=False,
        empty_label="All devices (broadcast)",
    )
    title = forms.CharField(max_length=200)
    body = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrap(self.fields)


class ReminderForm(forms.ModelForm):
    class Meta:
        model = AppReminder
        fields = ["account", "title", "body", "link", "scheduled_at", "recurrence", "is_active"]
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
        self.fields["link"].required = False
        self.fields["link"].empty_label = "— No link (just opens the app) —"
        # datetime-local sends "YYYY-MM-DDTHH:MM"; accept a couple of extra shapes too.
        self.fields["scheduled_at"].input_formats = [
            "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
        ]
        # Show the stored (UTC) time as local (IST) in the picker.
        if self.instance and self.instance.pk and self.instance.scheduled_at:
            self.initial["scheduled_at"] = timezone.localtime(self.instance.scheduled_at)
        _bootstrap(self.fields, checkbox_fields=("is_active",))
        for name in ("account", "link", "recurrence"):
            self.fields[name].widget.attrs["class"] = "form-select"

    def clean_scheduled_at(self):
        dt = self.cleaned_data.get("scheduled_at")
        # The picker is naive; interpret it in the server timezone (IST) before saving.
        if dt and timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone.get_current_timezone())
        return dt

    def clean(self):
        cleaned = super().clean()
        account = cleaned.get("account")
        link = cleaned.get("link")
        if link and not account:
            self.add_error("link", "Choose an account to attach a link (broadcasts can't use a per-account link).")
        elif link and account and link.account_id != account.id:
            self.add_error("link", "This link belongs to a different account.")
        return cleaned
