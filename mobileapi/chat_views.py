"""
User-facing web chat, opened inside the app's in-app WebView.

Auth model: the app calls the token-authed `GET /app/v1/chat/session` (see views.chat_session),
which mints a short-lived SIGNED token and returns a URL to `chat_open`. Opening that URL exchanges
the token for a normal Django session (scoped to the account) and redirects to the clean chat page.
All subsequent page + AJAX requests authenticate via that session cookie (same-origin), so the
bearer token never lands in the WebView/URL history. Kept server-rendered so the chat UI can be
iterated without shipping a new app build.
"""
import json
from datetime import timedelta

from django.core import signing
from django.db.models import Count, Max, Q
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from . import fcm
from .models import AppAccount, AppChatMessage, AppDevice
from .serializers import chat_message_dict

CHAT_SALT = "syncup-chat-web"
CHAT_TOKEN_MAX_AGE = 300  # seconds — the one-time open link is valid ~5 min
SESSION_KEY = "chat_account_id"


def make_open_token(account):
    """Signed, short-lived token the app hands to the WebView to start a chat session."""
    return signing.dumps({"account_id": account.id}, salt=CHAT_SALT)


def _session_account(request):
    account_id = request.session.get(SESSION_KEY)
    if not account_id:
        return None
    return AppAccount.objects.filter(id=account_id, is_active=True).first()


def _mark_admin_read(account):
    AppChatMessage.objects.filter(
        account=account, sender="admin", read_by_user=False,
    ).update(read_by_user=True)


def _touch_presence(account):
    """Record that the user is currently on the chat screen (so admin replies skip the push)."""
    AppAccount.objects.filter(pk=account.pk).update(chat_last_seen_at=timezone.now())


@require_http_methods(["GET"])
def chat_open(request):
    """Exchange the one-time signed token for a web session, then redirect to the chat page."""
    token = request.GET.get("t", "")
    try:
        data = signing.loads(token, salt=CHAT_SALT, max_age=CHAT_TOKEN_MAX_AGE)
    except signing.BadSignature:
        return HttpResponseForbidden("This chat link has expired. Please reopen chat from the app.")
    request.session[SESSION_KEY] = data["account_id"]
    request.session.set_expiry(8 * 60 * 60)  # 8 hours
    # Support agents get the inbox; everyone else gets their own 1:1 chat.
    account = AppAccount.objects.filter(id=data["account_id"]).first()
    if account and account.admin_chat_mode:
        return redirect("chat_inbox_home")
    return redirect("chat_home")


@require_http_methods(["GET"])
def chat_home(request):
    """The chat page itself (server-rendered; polls chat_messages via JS)."""
    account = _session_account(request)
    if not account:
        return HttpResponseForbidden("Please reopen chat from the app.")
    _mark_admin_read(account)
    _touch_presence(account)
    resp = render(request, "mobileapi/chat.html", {"account": account})
    resp["Cache-Control"] = "no-store"  # always load the latest chat UI (avoid stale WebView cache)
    return resp


@require_http_methods(["GET"])
def chat_messages(request):
    """JSON message list for polling. `since` = last message id the client already has."""
    account = _session_account(request)
    if not account:
        return JsonResponse({"error": "unauthorized"}, status=401)
    qs = AppChatMessage.objects.filter(account=account)
    since = request.GET.get("since")
    if since and since.isdigit():
        qs = qs.filter(id__gt=int(since))
    rows = [chat_message_dict(m) for m in qs]
    # Reading the thread clears the user's unread badge, and marks them "present" on chat.
    _mark_admin_read(account)
    _touch_presence(account)
    # Highest of the user's own messages that the admin has read → read receipts (✓ vs ✓✓).
    read_upto = (
        AppChatMessage.objects.filter(account=account, sender="user", read_by_admin=True)
        .aggregate(m=Max("id"))["m"] or 0
    )
    return JsonResponse({
        "messages": rows,
        "read_upto_id": read_upto,
        "admin_typing": account.is_admin_typing(),
        "admin_online": account.is_admin_online(),
    })


@require_http_methods(["POST"])
def chat_send(request):
    """User sends a message. CSRF-protected via the session (token embedded in the page)."""
    account = _session_account(request)
    if not account:
        return JsonResponse({"error": "unauthorized"}, status=401)
    try:
        payload = json.loads(request.body or b"{}")
    except (ValueError, TypeError):
        payload = {}
    body = (payload.get("body") or "").strip()
    if not body:
        return JsonResponse({"error": "empty"}, status=400)
    body = body[:4000]
    msg = AppChatMessage.objects.create(
        account=account, sender="user", body=body, read_by_user=True, read_by_admin=False,
    )
    # Clear the typing flag now that a message was sent.
    AppAccount.objects.filter(pk=account.pk).update(chat_typing_until=None)
    _notify_agents_new_message(account, body)
    return JsonResponse({"ok": True, "message": chat_message_dict(msg)})


@require_http_methods(["POST"])
def chat_typing(request):
    """The user is typing — set a short-lived flag the admin console shows as 'typing…'."""
    account = _session_account(request)
    if not account:
        return JsonResponse({"error": "unauthorized"}, status=401)
    AppAccount.objects.filter(pk=account.pk).update(
        chat_typing_until=timezone.now() + timedelta(seconds=6),
        chat_last_seen_at=timezone.now(),
    )
    return JsonResponse({"ok": True})


# --------------------------------------------------------------------------- #
# Mobile support-agent inbox (admin_chat_mode) — same session, gated on the flag
# --------------------------------------------------------------------------- #
def _session_agent(request):
    """The acting account IF it's a support agent (admin_chat_mode), else None."""
    account = _session_account(request)
    return account if (account and account.admin_chat_mode) else None


def _notify_agents_new_message(sender_account, body):
    """A user sent a message → push to ALL support agents' devices (any number of agents),
    skipping agents who are already active in the inbox."""
    agents = AppAccount.objects.filter(admin_chat_mode=True, is_active=True).exclude(pk=sender_account.pk)
    agents = [a for a in agents if not a.is_active_on_chat(20)]
    if not agents:
        return
    tokens = list(
        AppDevice.objects.filter(account__in=agents, is_active=True)
        .exclude(fcm_token="").exclude(fcm_token__isnull=True)
        .values_list("fcm_token", flat=True)
    )
    if tokens and fcm.is_configured():
        try:
            fcm.send(tokens, "New message from " + (sender_account.name or "a user"), body[:120], data={"type": "chat"})
        except Exception:
            pass


def _notify_admin_reply(target, body):
    """Push an agent's reply to the end user's devices, unless they're active on their chat."""
    if target.is_active_on_chat():
        return
    tokens = list(
        AppDevice.objects.filter(account=target, is_active=True)
        .exclude(fcm_token="").exclude(fcm_token__isnull=True)
        .values_list("fcm_token", flat=True)
    )
    if tokens and fcm.is_configured():
        try:
            fcm.send(tokens, "New message from Admin", body[:120], data={"type": "chat"})
        except Exception:
            pass


@require_http_methods(["GET"])
def chat_inbox_home(request):
    agent = _session_agent(request)
    if not agent:
        return HttpResponseForbidden("Please reopen chat from the app.")
    resp = render(request, "mobileapi/chat_inbox.html", {"agent": agent})
    resp["Cache-Control"] = "no-store"
    return resp


@require_http_methods(["GET"])
def chat_inbox_list(request):
    agent = _session_agent(request)
    if not agent:
        return JsonResponse({"error": "unauthorized"}, status=401)
    AppAccount.objects.filter(pk=agent.pk).update(chat_last_seen_at=timezone.now())  # agent is active
    accounts = (
        AppAccount.objects.filter(chat_messages__isnull=False).exclude(pk=agent.pk).distinct()
        .annotate(
            last_message_at=Max("chat_messages__created_at"),
            unread=Count(
                "chat_messages",
                filter=Q(chat_messages__sender="user", chat_messages__read_by_admin=False),
            ),
        )
        .order_by("-last_message_at")
    )
    data = []
    for a in accounts:
        last = a.chat_messages.order_by("-created_at").first()
        data.append({
            "id": a.id, "name": a.name, "email": a.email, "unread": a.unread,
            "last_body": (last.body[:90] if last else ""),
            "last_sender": (last.sender if last else ""),
            "last_at_ms": int(a.last_message_at.timestamp() * 1000) if a.last_message_at else 0,
            "online": a.is_active_on_chat(30),
            "last_seen_ms": int(a.chat_last_seen_at.timestamp() * 1000) if a.chat_last_seen_at else 0,
        })
    return JsonResponse({"conversations": data})


@require_http_methods(["GET"])
def chat_inbox_thread(request, account_id):
    agent = _session_agent(request)
    if not agent:
        return JsonResponse({"error": "unauthorized"}, status=401)
    target = get_object_or_404(AppAccount, id=account_id)
    if target.pk == agent.pk:
        return JsonResponse({"error": "forbidden"}, status=403)
    qs = target.chat_messages.all()
    since = request.GET.get("since")
    if since and since.isdigit():
        qs = qs.filter(id__gt=int(since))
    rows = [chat_message_dict(m) for m in qs]
    AppChatMessage.objects.filter(account=target, sender="user", read_by_admin=False).update(read_by_admin=True)
    # Agent is viewing this thread → the end user sees the "admin active" dot; and the agent
    # is marked active so they aren't pinged for new messages while looking.
    AppAccount.objects.filter(pk=target.pk).update(admin_last_seen_at=timezone.now())
    AppAccount.objects.filter(pk=agent.pk).update(chat_last_seen_at=timezone.now())
    read_upto = (
        AppChatMessage.objects.filter(account=target, sender="admin", read_by_user=True)
        .aggregate(m=Max("id"))["m"] or 0
    )
    return JsonResponse({
        "account": {
            "id": target.id, "name": target.name, "email": target.email,
            "online": target.is_active_on_chat(30),
            "last_seen_ms": int(target.chat_last_seen_at.timestamp() * 1000) if target.chat_last_seen_at else 0,
        },
        "messages": rows,
        "read_upto_id": read_upto,
        "user_typing": target.is_typing(),
    })


@require_http_methods(["POST"])
def chat_inbox_reply(request, account_id):
    agent = _session_agent(request)
    if not agent:
        return JsonResponse({"error": "unauthorized"}, status=401)
    target = get_object_or_404(AppAccount, id=account_id)
    if target.pk == agent.pk:
        return JsonResponse({"error": "forbidden"}, status=403)
    try:
        body = (json.loads(request.body or b"{}").get("body") or "").strip()
    except (ValueError, TypeError):
        body = ""
    if not body:
        return JsonResponse({"error": "empty"}, status=400)
    body = body[:4000]
    msg = AppChatMessage.objects.create(
        account=target, sender="admin", body=body, read_by_admin=True, read_by_user=False,
    )
    AppAccount.objects.filter(pk=target.pk).update(chat_admin_typing_until=None)
    _notify_admin_reply(target, body)
    return JsonResponse({"ok": True, "message": chat_message_dict(msg)})


@require_http_methods(["POST"])
def chat_inbox_typing(request, account_id):
    agent = _session_agent(request)
    if not agent:
        return JsonResponse({"error": "unauthorized"}, status=401)
    AppAccount.objects.filter(id=account_id).update(chat_admin_typing_until=timezone.now() + timedelta(seconds=6))
    return JsonResponse({"ok": True})
