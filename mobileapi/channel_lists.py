"""Channels: reading playlist links into the admin's own list, and serving that list.

The admin adds a playlist link and checks channels by hand (see channels_admin.py). Nothing here tests
a stream: there's no tester and no cron, by the user's decision. Only channels marked Working leave
this server, through one private link. The link reaches a phone only when the admin pushes it to
chosen accounts and the person saves it; the app is never fed a list by itself (see tv-station-plan
in the project notes for the Play Store reason).
"""
import hashlib
import logging
import re
import secrets
from urllib.parse import urlsplit, urlunsplit

import requests
from django.db import transaction
from django.http import Http404, HttpResponse
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.views.decorators.gzip import gzip_page
from django.views.decorators.http import require_GET

from .models import ChannelLink, ChannelSource, LiveChannel

logger = logging.getLogger(__name__)

MAX_PLAYLIST_BYTES = 30 * 1024 * 1024
READ_TIMEOUT_SECONDS = 30
USER_AGENT = "SyncUp-Admin/1.0"
IPTV_API_BASE = "https://iptv-org.github.io/api/"
_ATTR = re.compile(r'([A-Za-z0-9_-]+)="([^"]*)"')
_IPTV_ID = re.compile(r"\.[a-zA-Z]{2}(?:@|$)")
_ONE_CHANNEL = " If it's one channel, type its name to add it without reading it."


class ChannelLinkError(Exception):
    """A link that can't be read as a channel list. The message is for the admin."""


def is_channel_link(url):
    """A link the phone should offer to save in Channels, not open in a tab: by its address."""
    try:
        path = urlsplit((url or "").strip()).path
    except ValueError:
        return False
    return path.lower().endswith((".m3u", ".m3u8"))


def web_link(url):
    """[url] when it's a plain http(s) address, else "". http stays: many playlists are only that."""
    url = (url or "").strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    return url if parts.scheme.lower() in ("http", "https") and parts.hostname else ""


def stream_hash(url):
    """The same stream once, however its scheme or host is written."""
    parts = urlsplit(url.strip())
    same = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, parts.fragment))
    return hashlib.sha256(same.encode()).hexdigest()


def _clean(value, limit):
    """One line, no quotes: this goes back out into an M3U, where they'd break the entry."""
    return " ".join(str(value).replace('"', "'").split())[:limit]


def parse_playlist(text):
    """The channels of an M3U: each with its name, stream, groups and whether it needs special headers."""
    channels = []
    current = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#EXTINF"):
            attrs = dict(_ATTR.findall(line))
            # The name follows the first comma outside the attributes: their values can hold commas, and a name can hold quotes.
            bare = _ATTR.sub("", line)
            name = bare.split(",", 1)[1] if "," in bare else ""
            current = {
                "name": _clean(name, 200),
                "groups": [g.strip() for g in attrs.get("group-title", "").split(";") if g.strip()],
                "tvg_id": _clean(attrs.get("tvg-id", ""), 120),
                "logo": _clean(attrs.get("tvg-logo", ""), 1000),
                "needs_headers": bool(attrs.get("http-user-agent") or attrs.get("http-referrer")),
            }
        elif line.startswith("#EXTVLCOPT") and current is not None:
            current["needs_headers"] = True
        elif line and not line.startswith("#") and current is not None:
            if current["name"] and web_link(line):
                current["url"] = line
                channels.append(current)
            current = None
    return channels


def _fetch_playlist(url):
    try:
        with requests.get(url, timeout=READ_TIMEOUT_SECONDS, stream=True, headers={"User-Agent": USER_AGENT}) as resp:
            if resp.status_code != 200:
                raise ChannelLinkError(f"That link answered {resp.status_code}." + _ONE_CHANNEL)
            body = b""
            for chunk in resp.iter_content(1024 * 256):
                body += chunk
                if len(body) > MAX_PLAYLIST_BYTES:
                    raise ChannelLinkError("That link is too big to read.")
    except requests.RequestException:
        logger.info("Couldn't read channel link %s", url, exc_info=True)
        raise ChannelLinkError("Couldn't reach that link from the server." + _ONE_CHANNEL) from None
    return body.decode("utf-8", "replace")


def _languages():
    """iptv-org channel id → its main feed's first language name. {} when that can't be read."""
    try:
        feeds = requests.get(IPTV_API_BASE + "feeds.json", timeout=READ_TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT}).json()
        names = requests.get(IPTV_API_BASE + "languages.json", timeout=READ_TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT}).json()
        by_code = {row["code"]: row["name"] for row in names}
        return {
            f["channel"]: by_code.get(f["languages"][0], "")
            for f in feeds if f.get("is_main") and f.get("channel") and f.get("languages")
        }
    except (requests.RequestException, ValueError, KeyError, TypeError):
        logger.info("Couldn't read iptv-org's languages", exc_info=True)
        return {}


def read_link(url, leave_out_sports=True, leave_out_headers=True):
    """Read a playlist link into the list. Only what's new is added, as Not checked: a channel already
    here stays as the admin left it, removed ones included, and the same stream never appears twice."""
    url = web_link(url)
    if not url:
        raise ChannelLinkError("Enter an http or https link.")
    text = _fetch_playlist(url)
    if "#EXTM3U" not in text[:2048]:
        raise ChannelLinkError("That link isn't a channel list." + _ONE_CHANNEL)
    if "#EXT-X-STREAM-INF" in text or "#EXT-X-TARGETDURATION" in text:
        raise ChannelLinkError("That's one stream, not a list. Type its name to add it as one channel.")
    found = parse_playlist(text)
    if not found:
        raise ChannelLinkError("That link has no channels in it.")

    known = set(LiveChannel.objects.values_list("url_hash", flat=True))
    fresh = []
    sports = headers = already = 0
    for ch in found:
        if leave_out_sports and any(g.lower() == "sports" for g in ch["groups"]):
            sports += 1
        elif leave_out_headers and ch["needs_headers"]:
            headers += 1
        else:
            digest = stream_hash(ch["url"])
            if digest in known:
                already += 1
            else:
                known.add(digest)
                ch["hash"] = digest
                fresh.append(ch)

    tvg_ids = [ch["tvg_id"].split("@")[0] for ch in fresh if ch["tvg_id"]]
    # The language filter, for lists that use iptv-org's ids: others just have none.
    looks_iptv = sum(1 for i in tvg_ids if _IPTV_ID.search(i)) * 5 >= len(fresh) * 2 if fresh else False
    languages = _languages() if looks_iptv else {}

    with transaction.atomic():
        source, _ = ChannelSource.objects.update_or_create(url=url[:1000], defaults={
            "found": len(found), "added": len(fresh), "left_out_sports": sports, "left_out_headers": headers,
            "last_read_at": timezone.now(),
        })
        LiveChannel.objects.bulk_create([
            LiveChannel(
                url_hash=ch["hash"], url=ch["url"], name=ch["name"], group=_clean(ch["groups"][0], 80) if ch["groups"] else "",
                language=_clean(languages.get(ch["tvg_id"].split("@")[0], ""), 40), tvg_id=ch["tvg_id"], logo=ch["logo"], source=source,
            ) for ch in fresh
        ], batch_size=500)
    return {"found": len(found), "added": len(fresh), "already": already, "sports": sports, "headers": headers}


def add_stream(url, name):
    """Add one channel by its link, as Not checked. It isn't read: the admin tests it by hand like any
    other, and a stream this server can't reach may still play on a phone."""
    url = web_link(url)
    if not url or len(url.split()) != 1:
        raise ChannelLinkError("Enter an http or https link.")
    name = _clean(name, 200)
    if not name:
        raise ChannelLinkError("Enter the channel's name.")
    digest = stream_hash(url)
    here = LiveChannel.objects.filter(url_hash=digest).first()
    if here:
        raise ChannelLinkError(f"That link is already here as {here.name} ({here.get_status_display()}).")
    LiveChannel.objects.create(url_hash=digest, url=url, name=name)


def new_token():
    return secrets.token_urlsafe(16)


def list_text():
    """The Working channels, as an M3U. Its stored fields are already one line with no quotes."""
    lines = ["#EXTM3U"]
    for ch in LiveChannel.objects.filter(status=LiveChannel.OK).order_by("name"):
        lines.append(f'#EXTINF:-1 tvg-id="{ch.tvg_id}" tvg-logo="{ch.logo}" group-title="{ch.group}",{ch.name}')
        lines.append(ch.url)
    return "\n".join(lines) + "\n"


@require_GET
@gzip_page
def list_m3u(request, token):
    """The private link: anyone who has it can read the list, and New link stops the old one."""
    if not constant_time_compare(token, ChannelLink.load().token):
        raise Http404
    return HttpResponse(list_text(), content_type="audio/x-mpegurl; charset=utf-8")
