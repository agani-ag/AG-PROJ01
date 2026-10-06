"""Live Telegram delivery: Telegram POSTs each update to us instead of us polling for it.

Mounted at /telegram/hook/<secret>/ (see main/urls.py). Two gates, both needed:

  * the secret in the URL, generated here and only known to Telegram and us;
  * the X-Telegram-Bot-Api-Secret-Token header, which Telegram echoes back from setWebhook.

A webhook and getUpdates are mutually exclusive, so turning this on stops the cron scan (see
cron_views._telegram_tick) and turning it off resumes it. Updates land in the same ingest()
used by polling, so both paths behave identically.
"""
import hmac
import json
import logging
import secrets

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import telegram as tg
from . import telegram_chats
from .models import AppConfig

logger = logging.getLogger(__name__)

# Only the updates the console needs; anything else is wasted traffic.
ALLOWED_UPDATES = ["message", "edited_message", "channel_post", "my_chat_member", "chat_member"]


def is_live():
    """True when live delivery is switched on (we hold a secret for it)."""
    return bool((AppConfig.load().telegram_webhook_secret or "").strip())


def hook_url(request):
    """The public URL Telegram should post to, https even behind a TLS-terminating proxy."""
    cfg = AppConfig.load()
    secret = (cfg.telegram_webhook_secret or "").strip()
    if not secret:
        return ""
    url = request.build_absolute_uri(reverse("telegram_webhook", args=[secret]))
    host = request.get_host().split(":")[0]
    if url.startswith("http://") and host not in ("127.0.0.1", "localhost", "10.0.2.2"):
        url = "https://" + url[len("http://"):]
    return url


def enable(request):
    """Switch to live delivery. Returns (ok, message)."""
    if not tg.is_configured():
        return False, "Set the bot token first."
    cfg = AppConfig.load()
    cfg.telegram_webhook_secret = secrets.token_urlsafe(24)
    cfg.save(update_fields=["telegram_webhook_secret"])
    url = hook_url(request)
    if url.startswith("http://"):
        cfg.telegram_webhook_secret = ""
        cfg.save(update_fields=["telegram_webhook_secret"])
        return False, "Live updates need an https address — Telegram refuses plain http."
    ok, error = tg.set_webhook(url, cfg.telegram_webhook_secret, ALLOWED_UPDATES)
    if not ok:
        cfg.telegram_webhook_secret = ""
        cfg.save(update_fields=["telegram_webhook_secret"])
        return False, error or "Telegram rejected the webhook."
    return True, "Live updates are on — messages now arrive the moment they are sent."


def disable():
    """Go back to polling on the push cron. Returns (ok, message)."""
    ok, error = tg.delete_webhook()
    cfg = AppConfig.load()
    cfg.telegram_webhook_secret = ""
    cfg.save(update_fields=["telegram_webhook_secret"])
    if not ok:
        return False, error or "Telegram did not drop the webhook."
    return True, "Live updates are off. The push cron reads messages every 15 minutes again."


@csrf_exempt
@require_POST
def webhook(request, secret):
    """Telegram posts one update here. Answers 200 fast — Telegram retries anything else."""
    expected = (AppConfig.load().telegram_webhook_secret or "").strip()
    header = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not expected or not hmac.compare_digest(str(secret), expected):
        return HttpResponse(status=404)      # don't confirm the endpoint exists
    if header and not hmac.compare_digest(header, expected):
        return HttpResponse(status=404)
    try:
        update = json.loads(request.body or b"{}")
    except (ValueError, TypeError):
        return JsonResponse({"ok": False}, status=400)
    try:
        telegram_chats.ingest(update)
    except Exception as e:  # noqa: BLE001 — never make Telegram retry over our own bug
        logger.exception("Telegram webhook failed: %s", e)
    return JsonResponse({"ok": True})
