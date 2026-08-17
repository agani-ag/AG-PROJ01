"""Live Channels — Radio (audio) & TV (video) broadcast.

Computed-schedule sync: the server stores the ordered playlist + a single
`anchor_time` + a `version`. Viewers' browsers compute what's playing "now"
from the clock (no server job, no streaming through the server).

Public (no login):
  GET  /live/<slug>              - lean-back viewer page (already playing, locked)
  GET  /live/<slug>/state.json   - playlist + anchor + server clock (JSON)

Admin (superuser only):
  GET  /live/admin/<slug>              - manage tracks + broadcast controls
  POST /live/admin/<slug>/track/add    - add a track (URL + auto-probed duration)
  POST /live/admin/track/<id>/edit     - edit title/duration/active
  POST /live/admin/track/<id>/delete   - remove a track
  POST /live/admin/<slug>/reorder      - reorder tracks
  POST /live/admin/<slug>/control      - go_live / restart / skip / off_air
"""
from __future__ import annotations

import json
import time
import logging
import requests
from datetime import timedelta

from django.conf import settings
from django.db.models import Max
from django.http import JsonResponse, Http404
from django.shortcuts import render, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from ..models import LiveChannel, LiveTrack, BroadcastState, SignalMessage
from .device_access import _superuser_page, _superuser_api


# The two fixed channels. Seeded lazily so we never depend on a (gitignored) migration.
CHANNELS = {
    'radio': {'name': 'Radio', 'kind': 'audio'},
    'tv': {'name': 'TV', 'kind': 'video'},
}


def _get_channel(slug):
    meta = CHANNELS.get(slug)
    if not meta:
        return None
    ch, _ = LiveChannel.objects.get_or_create(
        slug=slug, defaults={'name': meta['name'], 'kind': meta['kind']},
    )
    return ch


def _active_tracks(ch):
    return list(ch.tracks.filter(is_active=True, duration_seconds__gt=0).order_by('order', 'id'))


# ====================================================================
# Public
# ====================================================================
@require_GET
def live_viewer(request, slug):
    ch = _get_channel(slug)
    if not ch:
        raise Http404("Unknown channel")
    return render(request, "device_access/live_viewer.html", {
        "channel": ch, "slug": ch.slug, "kind": ch.kind, "name": ch.name,
    })


@require_GET
def live_state(request, slug):
    ch = _get_channel(slug)
    if not ch:
        return JsonResponse({"error": "not found"}, status=404)

    playlist = [{
        "title": t.title or t.url,
        "url": t.url,
        "type": t.source_type,
        "duration": t.duration_seconds,
    } for t in _active_tracks(ch)]

    return JsonResponse({
        "slug": ch.slug,
        "name": ch.name,
        "kind": ch.kind,
        "is_live": ch.is_live,
        "loop": ch.loop,
        "version": ch.version,
        "anchor": ch.anchor_time.timestamp(),
        "server_now": timezone.now().timestamp(),
        "playlist": playlist,
    })


# ====================================================================
# Admin (superuser)
# ====================================================================
@_superuser_page
@require_GET
def live_admin(request, slug):
    ch = _get_channel(slug)
    if not ch:
        raise Http404("Unknown channel")
    tracks = list(ch.tracks.all().order_by('order', 'id'))
    total = sum(t.duration_seconds for t in tracks if t.is_active and t.duration_seconds)
    return render(request, "device_access/live_admin.html", {
        "channel": ch,
        "tracks": tracks,
        "total": total,
        "public_url": request.build_absolute_uri(reverse('live_viewer', args=[ch.slug])),
        "other_slug": 'tv' if ch.slug == 'radio' else 'radio',
    })


def _body(request):
    try:
        return json.loads(request.body or b"{}")
    except (ValueError, TypeError):
        return {}


@_superuser_api
@require_POST
def live_track_add(request, slug):
    ch = _get_channel(slug)
    if not ch:
        return JsonResponse({"success": False, "error": "not found"}, status=404)
    data = _body(request)
    url = (data.get("url") or "").strip()
    if not url:
        return JsonResponse({"success": False, "error": "URL required"}, status=400)

    stype = data.get("source_type") or "youtube"
    if stype not in dict(LiveTrack.SOURCE_CHOICES):
        stype = "youtube"
    try:
        dur = int(data.get("duration_seconds") or 0)
    except (ValueError, TypeError):
        dur = 0
    dur = max(0, dur)

    order = (ch.tracks.aggregate(m=Max("order"))["m"] or 0) + 1
    t = LiveTrack.objects.create(
        channel=ch, url=url, title=(data.get("title") or "").strip(),
        source_type=stype, duration_seconds=dur, order=order,
    )
    return JsonResponse({"success": True, "id": t.id})


@_superuser_api
@require_POST
def live_track_edit(request, track_id):
    t = get_object_or_404(LiveTrack, id=track_id)
    data = _body(request)
    if "title" in data:
        t.title = (data.get("title") or "").strip()
    if "duration_seconds" in data:
        try:
            t.duration_seconds = max(0, int(data.get("duration_seconds") or 0))
        except (ValueError, TypeError):
            pass
    if "is_active" in data:
        t.is_active = bool(data.get("is_active"))
    t.save()
    return JsonResponse({"success": True})


@_superuser_api
@require_POST
def live_track_delete(request, track_id):
    t = get_object_or_404(LiveTrack, id=track_id)
    t.delete()
    return JsonResponse({"success": True})


@_superuser_api
@require_POST
def live_track_reorder(request, slug):
    ch = _get_channel(slug)
    if not ch:
        return JsonResponse({"success": False, "error": "not found"}, status=404)
    ids = _body(request).get("order") or []
    for i, tid in enumerate(ids):
        ch.tracks.filter(id=tid).update(order=i + 1)
    return JsonResponse({"success": True})


@_superuser_api
@require_POST
def live_control(request, slug):
    ch = _get_channel(slug)
    if not ch:
        return JsonResponse({"success": False, "error": "not found"}, status=404)
    action = _body(request).get("action")
    n = timezone.now()

    if action == "go_live":
        ch.is_live = True
        ch.anchor_time = n
        ch.version += 1
    elif action == "restart":
        ch.anchor_time = n
        ch.version += 1
    elif action == "off_air":
        ch.is_live = False
        ch.version += 1
    elif action == "skip":
        # Re-anchor so the *next* track starts now.
        durs = [t.duration_seconds for t in _active_tracks(ch)]
        total = sum(durs)
        if total > 0:
            elapsed = (n.timestamp() - ch.anchor_time.timestamp()) % total
            acc = cur = 0
            for i, d in enumerate(durs):
                if elapsed < acc + d:
                    cur = i
                    break
                acc += d
            nxt = (cur + 1) % len(durs)
            cum_next = sum(durs[:nxt])
            ch.anchor_time = n - timedelta(seconds=cum_next)
            ch.version += 1
    else:
        return JsonResponse({"success": False, "error": "unknown action"}, status=400)

    ch.save()
    return JsonResponse({"success": True, "is_live": ch.is_live, "version": ch.version})


# ====================================================================
# Mode 3 — Live Broadcast (WebRTC)
# ====================================================================
BROADCAST_ROOM = "live"
HEARTBEAT_TTL = 15          # seconds — admin heartbeat freshness for "on air"
SIGNAL_TTL = 60            # seconds — signaling messages purged after this
MAX_SIGNAL_BYTES = 20000   # cap a single signaling payload


# Cache Metered's TURN credential list so we don't hit their API on every request.
_metered_cache = {"servers": None, "exp": 0.0}


def _metered_ice():
    """Fetch ready-to-use ICE servers from Metered's free TURN tier.

    Needs METERED_DOMAIN (e.g. 'yourapp.metered.live') + METERED_API_KEY. Returns
    a list of iceServers (their own STUN + TURN over UDP/TCP/TLS-443) or None.
    Cached for 1 hour; falls back to the last good list on a transient error.
    """
    domain = getattr(settings, "METERED_DOMAIN", "")
    key = getattr(settings, "METERED_API_KEY", "")
    if not (domain and key):
        return None
    now = time.time()
    if _metered_cache["exp"] > now:
        return _metered_cache["servers"]        # cached (success OR negative)
    try:
        r = requests.get(
            f"https://{domain}/api/v1/turn/credentials", params={"apiKey": key}, timeout=3,
        )
        r.raise_for_status()
        servers = r.json()
        if isinstance(servers, list) and servers:
            _metered_cache["servers"] = servers
            _metered_cache["exp"] = now + 3600
            return servers
    except Exception:
        logging.getLogger(__name__).exception("Metered TURN fetch failed")
    # Failure: negative-cache for 2 min so we never block state.json on every call
    # (e.g. hosts like PythonAnywhere free that can't reach Metered — use static TURN there).
    _metered_cache["exp"] = now + 120
    return _metered_cache["servers"]            # last good, or None


def _ice_servers():
    """ICE servers for WebRTC. STUN is always included (free). TURN is added when
    configured — preferring Metered's API-key tier, else static WEBRTC_TURN_URL
    (comma-separated URLs for UDP + TCP + TLS/443; TCP/443 is what gets through
    strict/mobile firewalls, essential for the internet case)."""
    servers = [{"urls": ["stun:stun.l.google.com:19302", "stun:stun1.l.google.com:19302"]}]

    metered = _metered_ice()
    if metered:
        servers.extend(metered)     # Metered returns its own STUN + TURN entries
        return servers

    turn_raw = getattr(settings, "WEBRTC_TURN_URL", "")
    if turn_raw:
        urls = [u.strip() for u in turn_raw.split(",") if u.strip()]
        if urls:
            s = {"urls": urls}
            if getattr(settings, "WEBRTC_TURN_USER", ""):
                s["username"] = settings.WEBRTC_TURN_USER
            if getattr(settings, "WEBRTC_TURN_CRED", ""):
                s["credential"] = settings.WEBRTC_TURN_CRED
            servers.append(s)
    return servers


def _broadcast_state():
    st, _ = BroadcastState.objects.get_or_create(room=BROADCAST_ROOM)
    return st


@require_GET
def broadcast_viewer(request):
    return render(request, "device_access/broadcast_viewer.html", {})


@_superuser_page
@require_GET
def broadcast_admin(request):
    return render(request, "device_access/broadcast_admin.html", {
        "public_url": request.build_absolute_uri(reverse("broadcast_viewer")),
    })


@require_GET
def broadcast_state_api(request):
    st = _broadcast_state()
    live = st.is_live and (timezone.now() - st.updated_at).total_seconds() < HEARTBEAT_TTL
    resp = {
        "is_live": live,
        "iceServers": _ice_servers(),
        "server_now": timezone.now().timestamp(),
    }
    # If the server can't reach Metered (e.g. PythonAnywhere free tier blocks
    # outbound), the browser fetches the TURN credentials itself — pass the config
    # through. NOTE: this exposes the Metered API key to the client (acceptable for
    # a private app; the key only grants TURN relay quota, nothing else).
    dom = getattr(settings, "METERED_DOMAIN", "")
    key = getattr(settings, "METERED_API_KEY", "")
    if dom and key:
        resp["metered"] = {"domain": dom, "apiKey": key}
    return JsonResponse(resp)


@_superuser_api
@require_POST
def broadcast_live_api(request):
    """Admin sets on-air/off-air + heartbeats (superuser only)."""
    st = _broadcast_state()
    st.is_live = bool(_body(request).get("is_live"))
    st.save()   # auto_now refreshes the heartbeat
    return JsonResponse({"success": True, "is_live": st.is_live})


@csrf_exempt
@require_POST
def broadcast_signal_api(request):
    """Public signaling relay — listeners aren't logged in. Just an SDP/ICE mailbox."""
    data = _body(request)
    to_peer = (data.get("to") or "").strip()[:64]
    from_peer = (data.get("from") or "").strip()[:64]
    kind = (data.get("kind") or "").strip()[:20]
    if not (to_peer and from_peer and kind):
        return JsonResponse({"success": False, "error": "missing fields"}, status=400)

    payload = data.get("data")
    text = payload if isinstance(payload, str) else json.dumps(payload or {})
    if len(text) > MAX_SIGNAL_BYTES:
        return JsonResponse({"success": False, "error": "payload too large"}, status=400)

    SignalMessage.objects.create(
        room=BROADCAST_ROOM, from_peer=from_peer, to_peer=to_peer, kind=kind, data=text,
    )
    # Opportunistic purge of stale messages (keeps the table tiny).
    SignalMessage.objects.filter(
        created_at__lt=timezone.now() - timedelta(seconds=SIGNAL_TTL)
    ).delete()
    return JsonResponse({"success": True})


@require_GET
def broadcast_poll_api(request):
    """Return signaling messages addressed to `peer` with id > `after` (cursor)."""
    peer = (request.GET.get("peer") or "").strip()[:64]
    try:
        after = int(request.GET.get("after") or "0")
    except (ValueError, TypeError):
        after = 0
    if not peer:
        return JsonResponse({"messages": [], "cursor": after})

    qs = SignalMessage.objects.filter(
        room=BROADCAST_ROOM, to_peer=peer, id__gt=after,
    ).order_by("id")[:50]
    msgs = [{"id": m.id, "from": m.from_peer, "kind": m.kind, "data": m.data} for m in qs]
    cursor = msgs[-1]["id"] if msgs else after
    return JsonResponse({"messages": msgs, "cursor": cursor})
