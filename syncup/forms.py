from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import AuthenticationForm

from .models import AdminPin


def _style(form):
    """app.css class for every widget (these forms have no checkboxes)."""
    for field in form.fields.values():
        field.widget.attrs.setdefault('class', 'input')


class AuthForm(AuthenticationForm):
    """Password login. Only superusers get through: AG-PROJ01 is admin-only."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)

    def clean_username(self):
        # Automatically convert the username to lowercase
        username = self.cleaned_data.get('username')
        return username.lower() if username else username

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not user.is_superuser:
            # Same message as a wrong password, so this doesn't reveal that the account exists.
            raise self.get_invalid_login_error()


# =============== Settings → Profile ===============
class AdminProfileForm(forms.Form):
    """Name, new password and new PIN in one form. A blank password or PIN keeps the current one.
    There's deliberately no current-password check: only the signed-in admin can reach the page."""
    name = forms.CharField(max_length=150, label='Name')   # stored on User.first_name (150 chars)
    new_password = forms.CharField(
        label='New password', required=False, strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
    )
    new_pin = forms.CharField(
        label='New PIN', required=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'off', 'maxlength': '5',
                                          'style': 'text-transform: uppercase'}),
    )

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        _style(self)

    def clean_new_password(self):
        password = self.cleaned_data.get('new_password') or ''
        if password:
            password_validation.validate_password(password, self.user)   # length / common / numeric rules
        return password

    def clean_new_pin(self):
        pin = (self.cleaned_data.get('new_pin') or '').strip().upper()
        if pin and not AdminPin.PATTERN.fullmatch(pin):
            raise forms.ValidationError('PIN must be exactly 5 letters or digits (A–Z, 0–9).')
        return pin
