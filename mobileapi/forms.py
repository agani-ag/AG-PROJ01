"""Forms for the HTML admin screens (Bootstrap-styled, matching the syncup app)."""
from django import forms

from .models import AppAccount, AppConfig, AppLink


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
        fields = ["title", "url", "description", "icon", "sort_order", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrap(self.fields, checkbox_fields=("is_active",))


class AppConfigForm(forms.ModelForm):
    class Meta:
        model = AppConfig
        fields = [
            "min_supported_version",
            "latest_version",
            "support_email",
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
