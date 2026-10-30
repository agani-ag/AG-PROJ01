"""UI template tags, registered as builtins in settings (no {% load %} needed).

    {% icon "plus" %}                      → <svg class="icon"><use href="/static/icons.svg?v=…#i-plus"></use></svg>
    {% icon "trash" "icon-sm" %}           → extra CSS classes
    {% nav_active "mobile_accounts mobile_account_edit" %}
                                           → aria-current="page" when the current URL name is in the list
    {{ some_datetime|ago }}                → "just now", "5 minutes ago", "2 days ago"
    {{ account.phone|tel }}               → +919876543210 (for href="tel:…")
    {% static_v "css/app.css" %}           → /static/css/app.css?v=… (changes when the file does)

Icon names are Lucide names; static/icons.svg lists the ones available.
"""
import hashlib
from functools import lru_cache

from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static
from django.utils.html import format_html
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.utils.timesince import timesince

register = template.Library()


@lru_cache(maxsize=32)
def _versioned(path):
    """The file's static URL with ?v=<short hash of its contents>. Our static files are served without
    cache headers, so browsers keep a copy and guess how long it stays fresh; a new address after
    each change (e.g. new icons in icons.svg) makes them fetch it again. Worked out once per process
    (a reload after deploying picks up the new file)."""
    found = finders.find(path)
    if not found:
        return static(path)
    with open(found, "rb") as f:
        return f"{static(path)}?v={hashlib.md5(f.read()).hexdigest()[:10]}"


@register.simple_tag
def static_v(path):
    return _versioned(path)


@register.simple_tag
def icon(name, css=""):
    return format_html(
        '<svg class="icon {}" aria-hidden="true"><use href="{}#i-{}"></use></svg>',
        css, _versioned("icons.svg"), name,
    )


@register.simple_tag(takes_context=True)
def nav_active(context, url_names):
    request = context.get("request")
    match = getattr(request, "resolver_match", None)
    if match is None:
        return ""
    current = match.url_name
    # "live_admin:radio" matches only when the slug kwarg is that value too.
    for entry in url_names.split():
        name, _, slug = entry.partition(":")
        if name == current and (not slug or match.kwargs.get("slug") == slug):
            return mark_safe('aria-current="page"')
    return ""


@register.filter
def tel(value):
    """A phone number as a tel: target — digits only, keeping a leading +, so a number stored
    as "+91 98765 43210" still dials."""
    raw = str(value or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    return ("+" if raw.startswith("+") else "") + digits


@register.filter
def ago(value):
    """Short relative time: the largest unit only ("2 days ago", not "2 days, 3 hours ago")."""
    if not value:
        return ""
    if (timezone.now() - value).total_seconds() < 60:
        return "just now"
    return timesince(value).split(",")[0] + " ago"
