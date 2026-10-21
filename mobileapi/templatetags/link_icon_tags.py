"""Link icons in the admin, drawn the way the app draws them (see link_icons.py).

    {% load link_icon_tags %}
    {% link_icon_css %}          → the styles, once per page
    {% link_icon link %}         → a link's tile: its picked icon in its colour, else (Auto) the
                                   website's favicon over its first letter
    {% icon_picker form %}       → the Auto / Pick-one chooser for a link form (icon + color fields)
"""
from urllib.parse import urlsplit

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

from .. import link_icons

register = template.Library()
PATHS = link_icons.svg_paths()
COLORS = {c[0]: c for c in link_icons.COLORS}


@register.simple_tag
def link_icon_css():
    return mark_safe("""<style>
.link-ico { display: inline-flex; align-items: center; justify-content: center; border-radius: 30%; flex: none; position: relative; overflow: hidden; vertical-align: middle; }
.link-ico svg { width: 58%; height: 58%; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
.link-ico.auto { background: var(--surface-2, #F1F5F9); border: 1px solid var(--border, #E2E8F0); color: var(--muted, #64748B); font-weight: 700; }
.link-ico.auto img { position: absolute; inset: 15%; width: 70%; height: 70%; object-fit: contain; background: #fff; border-radius: 4px; }
</style>""")


@register.simple_tag
def link_icon(link, size=34):
    icon = (link.icon or "").strip()
    if icon in PATHS:
        c = COLORS.get(link.color or "", COLORS[link_icons.DEFAULT_COLOR])
        return mark_safe(
            f'<span class="link-ico" style="width:{int(size)}px;height:{int(size)}px;background:{c[2]};color:{c[1]}" '
            f'title="{escape(icon)} · {escape(c[0])}"><svg viewBox="0 0 24 24" aria-hidden="true">{PATHS[icon]}</svg></span>'
        )
    host = urlsplit(link.url or "").hostname or ""
    letter = escape((link.title or "?").strip()[:1].upper() or "?")
    img = f'<img src="https://{escape(host)}/favicon.ico" alt="" loading="lazy" onerror="this.remove()">' if host else ""
    return mark_safe(
        f'<span class="link-ico auto" style="width:{int(size)}px;height:{int(size)}px;font-size:{int(size) // 2 - 2}px" '
        f'title="Auto — the website\'s own icon">{letter}{img}</span>'
    )


@register.inclusion_tag("mobileapi/_icon_picker.html")
def icon_picker(form):
    icon = (form["icon"].value() or "").strip()
    color = (form["color"].value() or "").strip()
    return {
        "groups": link_icons.GROUPS,
        "colors": link_icons.COLORS,
        "current_icon": icon,
        "current_color": color or link_icons.DEFAULT_COLOR,
        "legacy": icon if icon in link_icons.LEGACY else "",
        "legacy_path": PATHS.get(icon, ""),
        "errors": list(form["icon"].errors) + list(form["color"].errors),
        "title_id": form["title"].id_for_label,
        "url_id": form["url"].id_for_label,
    }
