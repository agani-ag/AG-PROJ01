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
from datetime import timedelta

from django.db.models import Max
from django.http import JsonResponse, Http404
from django.shortcuts import render, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from ..models import LiveChannel, LiveTrack
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
