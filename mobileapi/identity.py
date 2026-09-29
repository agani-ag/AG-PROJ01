"""Sign-in identifiers — one set of rules for sign-up, sign-in, profile edits and the admin form.

An account signs in with a password plus ONE of:
  * email    — stored lower-case
  * phone    — Indian mobile numbers only, stored as +91XXXXXXXXXX
  * username — 3–30 of a-z 0-9 . _ , stored lower-case, never all digits, no reserved names

Each normaliser returns (value, error): value is the stored form, error a user-facing message.
"""
import re

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

LOGIN_MODES = ("email", "phone", "username")

_USERNAME_RE = re.compile(r"^[a-z0-9._]{3,30}$")
RESERVED_USERNAMES = {
    "admin", "administrator", "root", "support", "help", "helpdesk", "syncup", "sync", "system",
    "official", "staff", "team", "security", "moderator", "owner", "api", "app", "null", "none",
    "undefined", "me", "you", "user", "users", "account", "accounts", "login", "signup", "settings",
}


def normalize_email(raw):
    email = (raw or "").strip().lower()
    if not email:
        return None, "Enter your email"
    try:
        validate_email(email)
    except ValidationError:
        return None, "Enter a valid email address"
    return email, None


def normalize_phone(raw):
    """India only. Accepts 98765 43210, 09876543210, 919876543210, +91 98765-43210."""
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        return None, "Enter your phone number"
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) != 10 or digits[0] not in "6789":
        return None, "Enter a valid 10-digit Indian mobile number"
    return f"+91{digits}", None


def normalize_username(raw):
    name = (raw or "").strip().lstrip("@").lower()
    if not name:
        return None, "Enter a username"
    if not _USERNAME_RE.match(name):
        return None, "Use 3–30 letters, numbers, dots or underscores"
    if name.isdigit():
        return None, "A username can't be only numbers"
    if name.startswith(".") or name.endswith(".") or ".." in name:
        return None, "A username can't start or end with a dot, or have two dots in a row"
    if name in RESERVED_USERNAMES:
        return None, "This username isn't available"
    return name, None


NORMALIZERS = {"email": normalize_email, "phone": normalize_phone, "username": normalize_username}

# Wording for a failed sign-in in each mode (never says which part was wrong).
BAD_LOGIN_MESSAGE = {
    "email": "Invalid email or password",
    "phone": "Invalid phone number or password",
    "username": "Invalid username or password",
}
