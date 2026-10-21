"""The icons a SyncUp link can show, and their colours — one list for the admin picker, the APIs
(SyncHub's hub/links, the Partner API) and what the app is sent.

A link shows, in order: its picked icon in its colour; else ("Auto", no icon) the website's own
icon, which the phone fetches; else a coloured letter. The app draws the icons itself (Material
icons), so a name here must also be known to the app — older apps show a letter for names they
don't know.
"""

# (group, [(name, label, svg path for the admin's own preview)]) — the picker, in this order.
GROUPS = [
    ("General", [
        ("link", "Link", '<path d="M10 14a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1"/><path d="M14 10a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1"/>'),
        ("globe", "Website", '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3.5 3 14.5 0 18M12 3c-3 3.5-3 14.5 0 18"/>'),
        ("star", "Star", '<path d="M12 3l2.8 5.7 6.2.9-4.5 4.4 1 6.2L12 17.3 6.5 20.2l1-6.2L3 9.6l6.2-.9z"/>'),
        ("home", "Home", '<path d="M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>'),
        ("bell", "Alerts", '<path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4z"/><path d="M10 20a2 2 0 0 0 4 0"/>'),
        ("person", "Account", '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>'),
        ("settings", "Settings", '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"/>'),
    ]),
    ("Health & wellbeing", [
        ("flower", "Flower", '<circle cx="12" cy="12" r="2.5"/><path d="M12 9.5C10 5 12 3 12 3s2 2 0 6.5M14.5 12c4.5-2 6.5 0 6.5 0s-2 2-6.5 0M12 14.5c2 4.5 0 6.5 0 6.5s-2-2 0-6.5M9.5 12C5 14 3 12 3 12s2-2 6.5 0"/>'),
        ("heart", "Heart", '<path d="M12 20s-7.5-4.6-7.5-10.2A4.2 4.2 0 0 1 12 7.2a4.2 4.2 0 0 1 7.5 2.6C19.5 15.4 12 20 12 20z"/>'),
        ("pill", "Medicine", '<rect x="2.5" y="8.5" width="19" height="7" rx="3.5" transform="rotate(-45 12 12)"/><path d="M9.5 9.5l5 5"/>'),
        ("fitness", "Fitness", '<path d="M3 9v6M6 7v10M18 7v10M21 9v6M6 12h12"/>'),
        ("leaf", "Leaf", '<path d="M5 19c0-9 6-14 15-14 0 9-5 15-14 15"/><path d="M5 19l8-8"/>'),
        ("drop", "Water", '<path d="M12 3c3.5 4.2 6 7.6 6 10.6A6 6 0 0 1 6 13.6C6 10.6 8.5 7.2 12 3z"/>'),
    ]),
    ("Chat & people", [
        ("chat", "Chat", '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>'),
        ("group", "Group", '<circle cx="9" cy="8" r="3.5"/><path d="M2 20a7 7 0 0 1 14 0M16 4.5a3.5 3.5 0 0 1 0 7M22 20a6 6 0 0 0-4-5.6"/>'),
        ("phone", "Call", '<path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2"/>'),
        ("mail", "Mail", '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/>'),
    ]),
    ("Money", [
        ("wallet", "Wallet", '<path d="M4 7h15a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h12"/><circle cx="16.5" cy="13.5" r="1.3"/>'),
        ("bank", "Bank", '<path d="M3 10l9-6 9 6M5 10v8M9.5 10v8M14.5 10v8M19 10v8M3 20h18"/>'),
        ("rupee", "Rupee", '<path d="M7 4h11M7 9h11M7 4c6 0 6 10 0 10l8 7"/>'),
        ("card", "Card", '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 10h18M7 15h4"/>'),
        ("cart", "Shop", '<circle cx="9" cy="20" r="1.4"/><circle cx="18" cy="20" r="1.4"/><path d="M2 3h3l2.5 12h11l2-8H6"/>'),
        ("receipt", "Bills", '<path d="M6 3h12v18l-3-2-3 2-3-2-3 2z"/><path d="M9 8h6M9 12h6"/>'),
    ]),
    ("Work & documents", [
        ("document", "Document", '<path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8z"/><path d="M14 3v5h5M9 13h6M9 17h6"/>'),
        ("folder", "Folder", '<path d="M3 6a1 1 0 0 1 1-1h5l2 2h9a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z"/>'),
        ("upload", "Upload", '<path d="M12 16V4M7 9l5-5 5 5M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/>'),
        ("briefcase", "Work", '<rect x="3" y="7" width="18" height="13" rx="2"/><path d="M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2M3 12h18"/>'),
        ("calendar", "Calendar", '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>'),
        ("chart", "Reports", '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>'),
        ("gov", "Government", '<path d="M14.5 12.5l-8 8a2.1 2.1 0 0 1-3-3l8-8"/><path d="M16 16l6-6M8 8l6-6M9 7l8 8M21 11l-8-8"/>'),
    ]),
    ("Learning, travel & media", [
        ("school", "School", '<path d="M2 9l10-5 10 5-10 5z"/><path d="M6 11v5c3 2 9 2 12 0v-5"/>'),
        ("book", "Book", '<path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2z"/><path d="M4 19a2 2 0 0 1 2-2h13"/>'),
        ("location", "Place", '<path d="M12 21s-7-6.2-7-11.5a7 7 0 0 1 14 0C19 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/>'),
        ("car", "Travel", '<path d="M5 16V11l2-5h10l2 5v5M3 16h18v3H3zM7 11h10"/>'),
        ("video", "Video", '<rect x="2" y="5" width="20" height="14" rx="3"/><path d="M10 9l5 3-5 3z"/>'),
        ("music", "Music", '<path d="M9 18V5l11-2v13"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="17.5" cy="16" r="2.5"/>'),
        ("camera", "Camera", '<path d="M4 8h3l2-3h6l2 3h3a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1z"/><circle cx="12" cy="13.5" r="3.5"/>'),
        ("image", "Picture", '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="9.5" r="1.8"/><path d="M21 16l-5-5-9 9"/>'),
        ("news", "News", '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 8h10M7 12h10M7 16h6"/>'),
    ]),
]

# Names from before the picker, still drawn by the app but not offered any more.
LEGACY = {
    "youtube": ("Video (old)", '<rect x="2" y="5" width="20" height="14" rx="3"/><path d="M10 9l5 3-5 3z"/>'),
    "notification": ("Alerts (old)", '<path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4z"/><path d="M10 20a2 2 0 0 0 4 0"/>'),
    "speed": ("Speed (old)", '<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>'),
    "newtab": ("New tab (old)", '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>'),
}

# name, light accent, light tile, dark accent, dark tile — the same values the app uses.
COLORS = [
    ("blue", "#1D4ED8", "#DBEAFE", "#93C5FD", "#1E3A8A"),
    ("teal", "#0F766E", "#CCFBF1", "#5EEAD4", "#134E4A"),
    ("green", "#15803D", "#DCFCE7", "#86EFAC", "#14532D"),
    ("lime", "#4D7C0F", "#ECFCCB", "#BEF264", "#1A2E05"),
    ("amber", "#B45309", "#FEF3C7", "#FCD34D", "#451A03"),
    ("orange", "#C2410C", "#FFEDD5", "#FDBA74", "#431407"),
    ("rose", "#BE123C", "#FFE4E6", "#FDA4AF", "#4C0519"),
    ("violet", "#6D28D9", "#EDE9FE", "#C4B5FD", "#3B2A63"),
    ("slate", "#334155", "#E2E8F0", "#CBD5E1", "#1E293B"),
]

PICKABLE = [name for _, items in GROUPS for name, _, _ in items]
ICONS = set(PICKABLE) | set(LEGACY)
COLOR_NAMES = [c[0] for c in COLORS]
DEFAULT_COLOR = "blue"


def choices():
    """For the model field: "" (Auto) first, then every name the app draws."""
    out = [("", "Auto (the website's own icon)")]
    out += [(name, label) for _, items in GROUPS for name, label, _ in items]
    out += [(name, label) for name, (label, _) in LEGACY.items()]
    return out


def color_choices():
    return [("", "Default")] + [(c[0], c[0].capitalize()) for c in COLORS]


def svg_paths():
    """{name: svg path} for the admin's icon sprite."""
    out = {name: path for _, items in GROUPS for name, _, path in items}
    out.update({name: path for name, (_, path) in LEGACY.items()})
    return out


def clean_icon(value):
    """(name or "", error or None). Empty = Auto."""
    v = (value or "").strip().lower() if isinstance(value, str) or value is None else None
    if v is None:
        return "", "icon must be text"
    if v and v not in ICONS:
        return "", f"unknown icon '{v}' — use one of: {', '.join(PICKABLE)}"
    return v, None


def clean_color(value):
    """(name or "", error or None). Empty = the default colour."""
    v = (value or "").strip().lower() if isinstance(value, str) or value is None else None
    if v is None:
        return "", "color must be text"
    if v and v not in COLOR_NAMES:
        return "", f"unknown color '{v}' — use one of: {', '.join(COLOR_NAMES)}"
    return v, None
