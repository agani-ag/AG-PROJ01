"""UI template tags, registered as builtins in settings (no {% load %} needed).

    {% icon "plus" %}                      → <svg class="icon"><use href="/static/icons.svg#i-plus"></use></svg>
    {% icon "trash" "icon-sm" %}           → extra CSS classes
    {% nav_active "mobile_accounts mobile_account_edit" %}
                                           → aria-current="page" when the current URL name is in the list

Icon names are Lucide names; static/icons.svg lists the ones available.
"""
from django import template
from django.templatetags.static import static
from django.utils.html import format_html
from django.utils.safestring import mark_safe

register = template.Library()


@register.simple_tag
def icon(name, css=""):
    return format_html(
        '<svg class="icon {}" aria-hidden="true"><use href="{}#i-{}"></use></svg>',
        css, static("icons.svg"), name,
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
