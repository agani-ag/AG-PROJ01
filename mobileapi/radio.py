"""Live radio channel registry — the receiver for AudioSync's presence protocol.

Broadcasters (AudioSync) POST register / heartbeat / stop events to `ingest` (shared Bearer key).
The app reads the live channel list from `channels` (app token). Liveness is heartbeat-driven and
evaluated at READ time — no cron: a channel is on air only while `is_live` AND its last heartbeat is
within AppConfig.radio_stale_after_seconds. Stale rows are pruned lazily whenever the list is read.
"""
import hmac
import json
from datetime import timedelta

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .auth import app_token_required
from .models import AppConfig, RadioChannel


def _err(msg, status=400):
    return JsonResponse({"success": False, "error": msg}, status=status)


def _int(v, default=0):
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return default


def _cadence(cfg):
    return {
        "heartbeat_interval": cfg.radio_heartbeat_interval_seconds,
        "stale_after": cfg.radio_stale_after_seconds,
    }


@csrf_exempt
@require_http_methods(["POST"])
def ingest(request):
    """Receive an AudioSync presence event. Bearer-key authenticated. Returns the heartbeat cadence
    (the POC adopts `heartbeat_interval`)."""
    cfg = AppConfig.load()
    key = (cfg.radio_ingest_key or "").strip()
    if not key:
        return _err("Radio ingest is not configured on the server", 503)
    auth = request.META.get("HTTP_AUTHORIZATION", "")
    token = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
    if not token or not hmac.compare_digest(token, key):
        return _err("Unauthorized", 401)

    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return _err("Invalid JSON")
    if not isinstance(data, dict):
        return _err("Invalid payload")

    event = (data.get("event") or "").strip()
    cadence = _cadence(cfg)

    # A connectivity test from the AudioSync settings dialog — validate the key, change nothing.
    if event == "test":
        return JsonResponse({"success": True, "message": "ok", **cadence})

    bid = (data.get("broadcaster_id") or "").strip()
    sid = (data.get("session_id") or "").strip()
    if not bid:
        return _err("broadcaster_id is required")

    if event == "public_url_active":
        stream_url = (data.get("stream_url") or "").strip()
        if not stream_url.lower().startswith(("http://", "https://")):
            return _err("stream_url (http/https) is required")
        ch, _created = RadioChannel.objects.update_or_create(
            broadcaster_id=bid,
            defaults={
                "name": (data.get("name") or "Radio").strip()[:120] or "Radio",
                "stream_url": stream_url[:500],
                "session_id": sid,
                "is_live": True,
                "now_playing": (data.get("now_playing") or "").strip()[:300],
                "listeners": _int(data.get("listeners")),
                "last_heartbeat_at": timezone.now(),
            },
        )
        return JsonResponse({"success": True, "channel_id": str(ch.id), **cadence})

    ch = RadioChannel.objects.filter(broadcaster_id=bid).first()

    if event == "heartbeat":
        # Ignore ghost heartbeats from a previous run (session_id must match the live session).
        if ch and (not ch.session_id or ch.session_id == sid):
            ch.session_id = sid or ch.session_id
            ch.is_live = True
            ch.now_playing = (data.get("now_playing") or "").strip()[:300]
            ch.listeners = _int(data.get("listeners"))
            if data.get("stream_url"):
                ch.stream_url = str(data["stream_url"]).strip()[:500]
            ch.last_heartbeat_at = timezone.now()
            ch.save(update_fields=[
                "session_id", "is_live", "now_playing", "listeners", "stream_url",
                "last_heartbeat_at", "updated_at",
            ])
        return JsonResponse({"success": True, **cadence})

    if event == "public_url_inactive":
        # Only the current session may retire the channel (a stale goodbye can't kill a newer run).
        if ch and (not ch.session_id or ch.session_id == sid):
            ch.delete()
        return JsonResponse({"success": True, **cadence})

    return _err("Unknown event")


@require_http_methods(["GET"])
@app_token_required
def channels(request):
    """The app's live channel list. `enabled` = master switch AND this user's per-user switch."""
    cfg = AppConfig.load()
    enabled = bool(cfg.radio_enabled and request.account.radio_enabled)
    if not enabled:
        return JsonResponse({"enabled": False, "channels": []})

    cutoff = timezone.now() - timedelta(seconds=cfg.radio_stale_after_seconds)
    live = RadioChannel.objects.filter(is_live=True, last_heartbeat_at__gte=cutoff).order_by("name")
    out = [{
        "id": str(c.id),
        "name": c.name,
        "stream_url": c.stream_url,
        "now_playing": c.now_playing,
        "listeners": c.listeners,
        "since": c.created_at.isoformat(),
    } for c in live]
    # No cron: prune channels that are flagged live but have gone stale, on read.
    RadioChannel.objects.filter(is_live=True, last_heartbeat_at__lt=cutoff).delete()
    return JsonResponse({"enabled": True, "channels": out})
