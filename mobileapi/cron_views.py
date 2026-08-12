"""
HTTP-triggered cron endpoints for the SyncUp mobile API — mounted at /cron/ (see main/urls.py).

There is no in-process scheduler: an external cron service calls these URLs on a schedule. That
shapes the whole design, because such a service can and will call an endpoint twice — it retries
on timeout, and two ticks overlap whenever a run outlasts its interval. So every endpoint here is

  * secret-gated  — these are public URLs (settings.CRON_KEY),
  * single-flight — a DB lock stops overlapping runs,
  * idempotent    — work is claimed with an atomic UPDATE before anything is sent, and
  * bounded       — each run stops at a wall-clock budget and reports what's left.

The push dispatcher sends AppReminder rows with delivery="cron". Those rows are never synced to
devices (see serializers.reminders_for), so the phone can't also fire them from a local alarm.
"""
import hmac
import time
from datetime import timedelta
from functools import wraps

from django.conf import settings
from django.db.models import F
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from . import fcm
from .cleanup import run_cleanup
from .models import AppDevice, AppNotificationLog, AppReminder, CronLock
from .serializers import reminder_tap_target

# Lock name for the push dispatcher (also read by the admin screen to show the last run).
PUSH_JOB = "push_dispatch"

# Lock name for the retention/cleanup job.
CLEANUP_JOB = "cleanup"

# Rows claimed per run. The wall-clock budget below is the real limiter; this only keeps the
# due-query bounded.
DEFAULT_BATCH = 25
MAX_BATCH = 200

# Stop claiming new work once a run has been going this long. Cron services typically abort the
# HTTP call around 30s and may then retry, so finishing early and reporting `remaining` is much
# safer than being killed mid-send.
BUDGET_SECONDS = 20

# A row left in "sending" for longer than this had its run die mid-flight (crash, deploy, aborted
# HTTP call). Put it back in the queue.
STUCK_AFTER = timedelta(minutes=10)

# Give up after this many dispatch attempts and park the row for a human.
MAX_ATTEMPTS = 3

# Never send a push more than this late — after an outage the whole backlog would otherwise land
# on users at once. Recurring rows skip forward instead of expiring.
MAX_DELAY = timedelta(hours=2)

# Longer than any single run, short enough that a crashed run frees the lock soon after.
LOCK_TTL_SECONDS = 15 * 60

# Per-token FCM timeout for cron sends. Well below the interactive default — sends are sequential,
# so one unreachable token must not eat the run's budget.
FCM_TIMEOUT = 5


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
def _authorized(request):
    expected = getattr(settings, "CRON_KEY", None)
    if not expected:
        return False  # unset key = endpoints closed, never open
    given = request.headers.get("X-Cron-Key") or request.GET.get("key", "")
    return hmac.compare_digest(str(given), str(expected))


def cron_endpoint(view):
    """Gate a view on the shared cron secret. GET and POST both allowed — plenty of cron
    services only issue GET."""

    @wraps(view)
    def inner(request, *args, **kwargs):
        if not _authorized(request):
            # 404 rather than 403: don't confirm the endpoint exists to anyone scanning.
            return HttpResponse(status=404)
        return view(request, *args, **kwargs)

    return csrf_exempt(require_http_methods(["GET", "POST"])(inner))


# --------------------------------------------------------------------------- #
# Health — call this first when wiring up the cron service
# --------------------------------------------------------------------------- #
@cron_endpoint
def health(request):
    lock = CronLock.objects.filter(name=PUSH_JOB).first()
    return JsonResponse({
        "ok": True,
        "now": timezone.now().isoformat(),
        "fcm_configured": fcm.is_configured(),
        "pending": _pending_qs().count(),
        "due_now": _due_qs(timezone.now()).count(),
        "last_push_run": lock.updated_at.isoformat() if lock else None,
    })


# --------------------------------------------------------------------------- #
# Push dispatcher
# --------------------------------------------------------------------------- #
@cron_endpoint
def dispatch_push(request):
    """Send every cron reminder whose time has arrived; leave future ones for a later tick.

    Safe to call at any interval — the due-query is `scheduled_at <= now`, so changing the cron
    from hourly to 15-minutely needs no code change. The interval only bounds how late a push
    can be.
    """
    if not CronLock.acquire(PUSH_JOB, LOCK_TTL_SECONDS):
        # A previous run is still going. Normal operation, not a failure — returning 5xx here
        # would set off the cron service's alerting for nothing.
        return JsonResponse({"ok": True, "skipped": "locked"})
    try:
        return JsonResponse(_dispatch(_batch_limit(request)))
    except Exception as e:  # noqa: BLE001 — surface any failure to the cron service as 5xx
        return JsonResponse({"ok": False, "error": str(e)[:500]}, status=500)
    finally:
        CronLock.release(PUSH_JOB)


# --------------------------------------------------------------------------- #
# Retention cleanup — call this once a day
# --------------------------------------------------------------------------- #
@cron_endpoint
def cleanup(request):
    """Prune old/expired rows per the admin's retention windows. Single-flight and idempotent —
    safe to call daily (or more often). Pass ?vacuum=1 to also reclaim disk space (off-peak)."""
    if not CronLock.acquire(CLEANUP_JOB, LOCK_TTL_SECONDS):
        return JsonResponse({"ok": True, "skipped": "locked"})
    try:
        do_vacuum = request.GET.get("vacuum") in ("1", "true", "yes")
        stats = run_cleanup(vacuum=do_vacuum)
        return JsonResponse({"ok": True, **stats})
    except Exception as e:  # noqa: BLE001 — surface any failure to the cron service as 5xx
        return JsonResponse({"ok": False, "error": str(e)[:500]}, status=500)
    finally:
        CronLock.release(CLEANUP_JOB)


def _batch_limit(request):
    try:
        return max(1, min(MAX_BATCH, int(request.GET.get("limit", DEFAULT_BATCH))))
    except (TypeError, ValueError):
        return DEFAULT_BATCH


def _pending_qs():
    return AppReminder.objects.filter(delivery="cron", is_active=True, status="pending")


def _due_qs(now):
    return _pending_qs().filter(scheduled_at__lte=now)


def _dispatch(limit):
    started = time.monotonic()
    now = timezone.now()
    stats = {
        "ok": True, "sent": 0, "failed": 0, "retrying": 0,
        "no_devices": 0, "requeued": 0, "expired": 0, "rescheduled": 0,
    }

    requeued, _parked = _reap_stuck(now)
    stats["requeued"] = requeued
    stats["expired"], stats["rescheduled"] = _expire_stale(now)

    due = list(
        _due_qs(now).select_related("account", "link").order_by("scheduled_at")[:limit]
    )
    for reminder in due:
        if time.monotonic() - started > BUDGET_SECONDS:
            break  # out of budget — the rest is reported as `remaining` and picked up next tick
        outcome = _send_one(reminder)
        if outcome in stats:
            stats[outcome] += 1

    stats["remaining"] = _due_qs(timezone.now()).count()
    stats["duration_ms"] = int((time.monotonic() - started) * 1000)
    return stats


def _reap_stuck(now):
    """Recover rows whose dispatch died mid-send. Returns (requeued, parked)."""
    stuck = AppReminder.objects.filter(
        delivery="cron", status="sending", claimed_at__lt=now - STUCK_AFTER,
    )
    # Under the attempt cap, put it back in the queue. This can in principle re-send a push that
    # actually did go out — a rare duplicate is the better failure than one that silently never
    # arrives. Past the cap, park it for a human rather than looping forever.
    requeued = stuck.filter(attempts__lt=MAX_ATTEMPTS).update(status="pending", claimed_at=None)
    parked = stuck.filter(attempts__gte=MAX_ATTEMPTS).update(
        status="failed", claimed_at=None,
        last_error="Dispatch died mid-send and the attempt limit was reached.",
    )
    return requeued, parked


def _expire_stale(now):
    """Handle pushes that are late by more than MAX_DELAY. Returns (expired, rescheduled)."""
    expired = rescheduled = 0
    for r in _pending_qs().filter(scheduled_at__lt=now - MAX_DELAY).iterator():
        # A recurring push just skips forward to its next slot; only a one-off is genuinely
        # too late to be worth sending.
        nxt = r.next_occurrence(now)
        if nxt:
            rescheduled += AppReminder.objects.filter(pk=r.pk, status="pending").update(
                scheduled_at=nxt,
            )
        else:
            hours = int(MAX_DELAY.total_seconds() // 3600)
            expired += AppReminder.objects.filter(pk=r.pk, status="pending").update(
                status="expired",
                last_error=f"Not sent within {hours}h of its scheduled time.",
            )
    return expired, rescheduled


def _tokens_for(reminder):
    """Active FCM tokens for this reminder's target (its account, or every account if broadcast)."""
    qs = (
        AppDevice.objects.filter(is_active=True, account__is_active=True)
        .exclude(fcm_token="").exclude(fcm_token__isnull=True)
    )
    if reminder.account_id:
        qs = qs.filter(account_id=reminder.account_id)
    return list(qs.values_list("fcm_token", flat=True))


def _send_one(reminder):
    """Claim, send, and record one reminder. Returns a stats key."""
    now = timezone.now()
    # Atomic claim, and the whole reason a user can't get this push twice. `select_for_update()`
    # is a no-op on SQLite, so the conditional UPDATE — which *is* atomic — does the work: only
    # one caller can move the row out of "pending", and only that caller goes on to send.
    won = AppReminder.objects.filter(pk=reminder.pk, status="pending").update(
        status="sending", claimed_at=now, attempts=F("attempts") + 1,
    )
    if won != 1:
        return "claimed_elsewhere"

    if not fcm.is_configured():
        AppReminder.objects.filter(pk=reminder.pk).update(
            status="pending", claimed_at=None, last_error="FCM is not configured.",
        )
        return "retrying"

    tokens = _tokens_for(reminder)
    if not tokens:
        # No devices to send to. Complete it rather than retrying forever.
        _finish(reminder, ok=0, fail=0, error="No active devices for this target.")
        return "no_devices"

    url, link_title = reminder_tap_target(reminder)
    data = {"link_url": url, "link_title": link_title} if url else None
    try:
        ok, fail = fcm.send(
            tokens, reminder.title, reminder.body, data,
            reminder.image_url or None, timeout=FCM_TIMEOUT,
        )
    except Exception as e:  # noqa: BLE001 — network/credential failures shouldn't kill the run
        msg = str(e)[:500]
        if reminder.attempts + 1 < MAX_ATTEMPTS:
            AppReminder.objects.filter(pk=reminder.pk).update(
                status="pending", claimed_at=None, last_error=msg,
            )
            return "retrying"
        AppReminder.objects.filter(pk=reminder.pk).update(
            status="failed", claimed_at=None, last_error=msg,
        )
        return "failed"

    AppNotificationLog.objects.create(
        account=reminder.account, title=reminder.title, body=reminder.body,
        data={"link_url": url} if url else None,
        success_count=ok, fail_count=fail,
    )
    _finish(reminder, ok, fail)
    return "sent" if ok else "failed"


def _finish(reminder, ok, fail, error=None):
    """Record the send, then either re-arm the next occurrence or mark the row done."""
    now = timezone.now()
    reminder.fires_done += 1  # so next_occurrence() sees the updated count for "Repeat N times"
    nxt = reminder.next_occurrence(now)
    fields = {
        "sent_at": now,
        "claimed_at": None,
        "fires_done": reminder.fires_done,
        "success_count": F("success_count") + ok,
        "fail_count": F("fail_count") + fail,
        "last_error": error,
        "status": "pending" if nxt else "sent",
    }
    if nxt:
        fields["scheduled_at"] = nxt
    AppReminder.objects.filter(pk=reminder.pk).update(**fields)
