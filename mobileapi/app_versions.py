"""SyncUp's app releases: the version name people see ("7.1") and its Play version code (9).

Phones report the name (device/hello, sign-in); the app's force-update check (AppConfig.
min_supported_version) compares the code. Add each new Play release to RELEASES.
"""
from django.db.models import Count

from .models import AppDevice

# (version name shown in the app, Play version code) — oldest first. v7 is code 8: code 7 only
# reached internal testing.
RELEASES = [
    ("5", 5),
    ("6", 6),
    ("7", 8),
    ("7.1", 9),
]


def code_for(name):
    """The Play version code for a reported version name; 0 when unknown. A name that isn't in
    RELEASES (an old build) counts as its major number ("3" → 3, "1.0" → 1)."""
    name = (name or "").strip()
    for n, code in RELEASES:
        if n == name:
            return code
    try:
        return int(name.split(".")[0])
    except ValueError:
        return 0


def name_for(code):
    """"7.1" for 9; "" when it isn't a release."""
    return next((n for n, c in RELEASES if c == code), "")


def minimum_choices(current):
    """The admin's choices for the force-update minimum: none, or "<release> or newer", each with how
    many active phones (signed in or not) would get the blocking update screen."""
    versions = list(AppDevice.objects.filter(is_active=True).values("app_version").annotate(n=Count("id")))

    def blocked(code):
        n = sum(v["n"] for v in versions if code_for(v["app_version"]) < code)
        return f"{n} phone{'' if n == 1 else 's'} would have to update"

    choices = [(0, "No forced update — every version opens SyncUp")]
    for name, code in RELEASES:
        choices.append((code, f"{name} or newer (code {code}) — {blocked(code)}"))
    if current and current not in {c for _, c in choices}:
        choices.append((current, f"Code {current}, as saved — {blocked(current)}"))
    return sorted(choices)
