"""The admin's Channels page (superuser-only), mounted at /mobile/channels.

One list, one private link. The admin adds playlist links, tests channels by hand in the browser and
marks each one Working or removes it. The Send tab pushes the list's link, or any other link, to chosen
accounts: the phone then opens Channels with the name and link filled in, for the person to save.
"""
import json
from urllib.parse import urlsplit

from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from . import fcm
from .admin_views import superuser_required
from .channel_lists import ChannelLinkError, new_token, read_link, web_link
from .models import AppAccount, AppDevice, ChannelLink, ChannelSource, LiveChannel

PAGE_SIZE = 50
MAX_NAME = 60


def _bad(message, status=400):
    return JsonResponse({"error": message}, status=status)


def _body(request):
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _counts():
    found = dict(LiveChannel.objects.values_list("status").annotate(n=Count("id")).order_by())
    return {status: found.get(status, 0) for status, _ in LiveChannel.STATUS}


def _list_url(request, link):
    return request.build_absolute_uri(reverse("channel_list_m3u", args=[link.token]))


def _state(request):
    """Everything on the page that isn't the table."""
    link = ChannelLink.load()
    live = LiveChannel.objects.exclude(status=LiveChannel.REMOVED)
    return {
        "counts": _counts(),
        "groups": sorted(set(live.exclude(group="").values_list("group", flat=True))),
        "languages": sorted(set(live.exclude(language="").values_list("language", flat=True))),
        "sources": [
            {
                "id": s.id, "url": s.url, "found": s.found, "added": s.added, "sports": s.left_out_sports,
                "headers": s.left_out_headers, "read": timezone.localtime(s.last_read_at).strftime("%d %b, %I:%M %p"),
            } for s in ChannelSource.objects.order_by("-last_read_at")
        ],
        "link": _list_url(request, link),
        "name": link.name,
    }


def _matching(filters):
    """The channels a table's filters show."""
    status = filters.get("status")
    if status not in dict(LiveChannel.STATUS):
        status = LiveChannel.NEW
    rows = LiveChannel.objects.filter(status=status)
    q = str(filters.get("q") or "").strip()
    if q:
        rows = rows.filter(Q(name__icontains=q) | Q(group__icontains=q))
    if filters.get("group"):
        rows = rows.filter(group=str(filters["group"]))
    if filters.get("language"):
        rows = rows.filter(language=str(filters["language"]))
    return rows


@superuser_required
@require_GET
def page(request):
    return render(request, "mobileapi/channels.html", {"state": _state(request), "page_size": PAGE_SIZE})


@superuser_required
@require_GET
def rows(request):
    """One page of the table."""
    matching = _matching(request.GET)
    total = matching.count()
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    try:
        number = min(max(1, int(request.GET.get("page", 1))), pages)
    except ValueError:
        number = 1
    start = (number - 1) * PAGE_SIZE
    return JsonResponse({
        "rows": [
            {
                "id": c.id, "name": c.name, "group": c.group, "language": c.language, "url": c.url, "logo": c.logo,
                "status": c.status, "http": c.url.lower().startswith("http://"),
            } for c in matching.order_by("name", "id")[start:start + PAGE_SIZE]
        ],
        "total": total, "page": number, "pages": pages, "counts": _counts(),
    })


@superuser_required
@require_POST
def mark(request):
    """Working, Removed, or back to Not checked: the given channels, or all that the filters match."""
    body = _body(request)
    status = body.get("status")
    if status not in dict(LiveChannel.STATUS):
        return _bad("Unknown status.")
    if body.get("all"):
        chosen = _matching(body.get("filters") if isinstance(body.get("filters"), dict) else {})
    else:
        ids = [i for i in body.get("ids") or [] if isinstance(i, int)]
        if not ids:
            return _bad("Pick some channels first.")
        chosen = LiveChannel.objects.filter(id__in=ids)
    changed = chosen.update(status=status, checked_at=None if status == LiveChannel.NEW else timezone.now())
    return JsonResponse({"changed": changed, "counts": _counts()})


@superuser_required
@require_POST
def read(request):
    """Add a link, or read one again."""
    body = _body(request)
    try:
        result = read_link(body.get("url"), bool(body.get("leave_out_sports", True)), bool(body.get("leave_out_headers", True)))
    except ChannelLinkError as e:
        return _bad(str(e))
    return JsonResponse({"result": result, "state": _state(request)})


@superuser_required
@require_POST
def new_link(request):
    """The old link stops at once, for everyone who saved it, until they're sent this one."""
    link = ChannelLink.load()
    link.token = new_token()
    link.save(update_fields=["token"])
    return JsonResponse({"link": _list_url(request, link)})


@superuser_required
@require_GET
def accounts(request):
    q = request.GET.get("q", "").strip()
    found = AppAccount.objects.filter(is_active=True)
    if q:
        found = found.filter(Q(name__icontains=q) | Q(email__icontains=q) | Q(phone__icontains=q) | Q(username__icontains=q))
    return JsonResponse({"accounts": [
        {"id": a.id, "name": a.name, "detail": a.email or a.phone or a.username or ""}
        for a in found.order_by("name")[:50]
    ]})


@superuser_required
@require_POST
def send(request):
    """Push the list's link, or any link, to chosen accounts. Tapping it opens Channels, filled in."""
    body = _body(request)
    if body.get("mode") == "any":
        url = web_link(body.get("link"))
        if not url:
            return _bad("Enter an http or https link.")
    else:
        url = _list_url(request, ChannelLink.load())
    title = str(body.get("title") or "").strip()[:200]
    message = str(body.get("body") or "").strip()
    if not title or not message:
        return _bad("Enter a title and a message.")
    ids = [i for i in body.get("accounts") or [] if isinstance(i, int)]
    chosen = list(AppAccount.objects.filter(is_active=True, id__in=ids))
    if not chosen:
        return _bad("Pick who gets it.")
    name = str(body.get("name") or "").strip()[:MAX_NAME] or (urlsplit(url).hostname or "")[:MAX_NAME]
    data = {"link_url": url, "open_in": "channels", "link_title": name}

    delivered = failed = 0
    without_device = []
    for account in chosen:
        tokens = list(AppDevice.objects.filter(is_active=True, account=account).values_list("fcm_token", flat=True))
        result = fcm.push(tokens, title, message, source="admin", account=account, data=data)
        if result.status == "not_configured":
            return _bad("FCM is not configured on this server.", 503)
        if result.status == "no_devices":
            without_device.append(account.name)
        delivered += result.delivered
        failed += result.failed
    return JsonResponse({"delivered": delivered, "failed": failed, "without_device": without_device})
