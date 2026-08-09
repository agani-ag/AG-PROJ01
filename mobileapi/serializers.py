"""Plain dict builders for JSON responses (no DRF)."""


def account_dict(account):
    return {
        "id": str(account.id),
        "name": account.name,
        "email": account.email,
    }


def link_dict(link):
    return {
        "id": str(link.id),
        "title": link.title,
        "url": link.url,
        "description": link.description or "",
        "icon": link.icon or "",
    }


def links_for(account):
    """Active links for an account, ordered (matches the app's UrlItem list)."""
    return [link_dict(link) for link in account.links.filter(is_active=True)]
