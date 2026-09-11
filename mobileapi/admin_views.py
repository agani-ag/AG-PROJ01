"""
Custom HTML admin screens for the SyncUp mobile API (superuser-only).

Function-based views + Bootstrap templates, matching the existing syncup app.
Mounted at /mobile/ (see admin_urls.py). Separate from the JSON API (/app/v1/).
"""
import json
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.db.models import Count, Max, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .serializers import chat_message_dict

from . import cron_views
from . import fcm
from . import remoteconfig as rc
from .forms import AppAccountForm, AppConfigForm, AppLinkForm, GeneralLinkForm, PushForm, ReminderForm
from . import partner_api
from .models import (
    AppAccount,
    AppActionRequest,
    AppChatMessage,
    AppConfig,
    AppDevice,
    AppLink,
    AppNotificationLog,
    AppPartner,
    AppReminder,
    CronLock,
)

superuser_required = user_passes_test(
    lambda u: u.is_authenticated and u.is_superuser, login_url=settings.LOGIN_URL
)


# ------------------------------------------------------------------ Dashboard
@superuser_required
def dashboard(request):
    ctx = {
        "account_count": AppAccount.objects.count(),
        "active_account_count": AppAccount.objects.filter(is_active=True).count(),
        "device_count": AppDevice.objects.filter(is_active=True).count(),
        "link_count": AppLink.objects.count(),
        "reminder_count": AppReminder.objects.filter(is_active=True).count(),
        "recent_accounts": AppAccount.objects.order_by("-created_at")[:5],
        "recent_notifications": AppNotificationLog.objects.all()[:5],
        "fcm_configured": fcm.is_configured(),
        "chat_unread_count": AppChatMessage.objects.filter(sender="user", read_by_admin=False).count(),
    }
    return render(request, "mobileapi/dashboard.html", ctx)


# ------------------------------------------------------------------ Accounts
@superuser_required
def accounts(request):
    qs = AppAccount.objects.select_related("partner").annotate(
        link_count=Count("links", distinct=True),
        device_count=Count("devices", distinct=True),
    )
    # Filter by origin: "admin" = admin-created (no partner), "<id>" = one partner, "" = all.
    selected = (request.GET.get("partner") or "").strip()
    if selected == "admin":
        qs = qs.filter(partner__isnull=True)
    elif selected.isdigit():
        qs = qs.filter(partner_id=int(selected))
    partners = AppPartner.objects.annotate(
        user_count=Count("accounts", distinct=True),
    ).order_by("name")
    return render(request, "mobileapi/accounts.html", {
        "accounts": qs,
        "partners": partners,
        "selected_partner": selected,
        "admin_count": AppAccount.objects.filter(partner__isnull=True).count(),
        "total_count": AppAccount.objects.count(),
    })


@superuser_required
def account_add(request):
    if request.method == "POST":
        form = AppAccountForm(request.POST)
        if form.is_valid():
            account = form.save()
            messages.success(request, "Account created.")
            return redirect("mobile_account_edit", account.id)
        messages.error(request, form.errors.as_text())
    else:
        form = AppAccountForm()
    return render(request, "mobileapi/account_edit.html", {"form": form})


@superuser_required
def account_edit(request, account_id):
    account = get_object_or_404(AppAccount, id=account_id)
    if request.method == "POST":
        form = AppAccountForm(request.POST, instance=account)
        if form.is_valid():
            form.save()
            messages.success(request, "Account saved.")
            return redirect("mobile_account_edit", account.id)
        messages.error(request, form.errors.as_text())
    else:
        form = AppAccountForm(instance=account)
    return render(request, "mobileapi/account_edit.html", {
        "form": form,
        "account": account,
        "is_edit": True,
        "links": account.links.all(),
        "devices": account.devices.all(),
    })


@superuser_required
@require_POST
def account_delete(request, account_id):
    account = get_object_or_404(AppAccount, id=account_id)
    account.delete()
    messages.success(request, "Account deleted.")
    return redirect("mobile_accounts")


# ------------------------------------------------------------------ Links
@superuser_required
def link_add(request):
    account_id = request.GET.get("account") or request.POST.get("account")
    account = get_object_or_404(AppAccount, id=account_id)
    if request.method == "POST":
        form = AppLinkForm(request.POST)
        if form.is_valid():
            link = form.save(commit=False)
            link.account = account
            link.save()
            messages.success(request, "Link added.")
            return redirect("mobile_account_edit", account.id)
        messages.error(request, form.errors.as_text())
    else:
        form = AppLinkForm()
    return render(request, "mobileapi/link_edit.html", {
        "form": form, "account": account, "all_urls": _distinct_link_urls(),
    })


@superuser_required
def link_edit(request, link_id):
    link = get_object_or_404(AppLink, id=link_id)
    account = link.account
    if request.method == "POST":
        form = AppLinkForm(request.POST, instance=link)
        if form.is_valid():
            form.save()
            messages.success(request, "Link saved.")
            return redirect("mobile_account_edit", account.id)
        messages.error(request, form.errors.as_text())
    else:
        form = AppLinkForm(instance=link)
    return render(request, "mobileapi/link_edit.html", {
        "form": form, "account": account, "link": link, "is_edit": True,
        "all_urls": _distinct_link_urls(),
    })


@superuser_required
@require_POST
def link_delete(request, link_id):
    link = get_object_or_404(AppLink, id=link_id)
    account_id = link.account_id
    link.delete()
    messages.success(request, "Link deleted.")
    if account_id is None:
        return redirect("mobile_general_links")
    return redirect("mobile_account_edit", account_id)


# ------------------------------------------------------------------ General links
# Links with no account — shown to every user whose account has show_general_links on.
@superuser_required
def general_links(request):
    links = AppLink.objects.filter(account__isnull=True).order_by("title")
    return render(request, "mobileapi/general_links.html", {"links": links})


@superuser_required
def general_link_add(request):
    if request.method == "POST":
        form = GeneralLinkForm(request.POST)
        if form.is_valid():
            link = form.save(commit=False)
            link.account = None  # general
            link.save()
            messages.success(request, "General link added.")
            return redirect("mobile_general_links")
        messages.error(request, form.errors.as_text())
    else:
        form = GeneralLinkForm()
    return render(request, "mobileapi/general_link_edit.html", {
        "form": form, "all_urls": _distinct_link_urls(),
    })


@superuser_required
def general_link_edit(request, link_id):
    link = get_object_or_404(AppLink, id=link_id, account__isnull=True)
    if request.method == "POST":
        form = GeneralLinkForm(request.POST, instance=link)
        if form.is_valid():
            form.save()
            messages.success(request, "General link saved.")
            return redirect("mobile_general_links")
        messages.error(request, form.errors.as_text())
    else:
        form = GeneralLinkForm(instance=link)
    return render(request, "mobileapi/general_link_edit.html", {
        "form": form, "link": link, "is_edit": True, "all_urls": _distinct_link_urls(),
    })


# ------------------------------------------------------------------ Partners (B2B API)
@superuser_required
def partners(request):
    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        contact = (request.POST.get("contact_email") or "").strip()
        if not name:
            messages.error(request, "Partner name is required.")
        else:
            partner, raw_key = AppPartner.issue(name, contact)
            # API key is shown once (only the hash is stored). The signing secret is also on the
            # partner row below (needed to verify action callbacks).
            messages.success(
                request,
                f"Partner “{partner.name}” created. API key (copy now, shown only once): {raw_key} · "
                f"Signing secret (for callback verification): {partner.signing_secret}",
            )
        return redirect("mobile_partners")
    qs = AppPartner.objects.annotate(user_count=Count("accounts", distinct=True))
    return render(request, "mobileapi/partners.html", {"partners": qs})


@superuser_required
@require_POST
def partner_regenerate(request, partner_id):
    partner = get_object_or_404(AppPartner, id=partner_id)
    raw_key = partner.regenerate_key()
    messages.success(
        request,
        f"New API key for “{partner.name}” (copy it now, shown only once): {raw_key}. "
        "The old key stops working immediately.",
    )
    return redirect("mobile_partners")


@superuser_required
@require_POST
def partner_toggle(request, partner_id):
    partner = get_object_or_404(AppPartner, id=partner_id)
    partner.is_active = not partner.is_active
    partner.save(update_fields=["is_active"])
    messages.success(request, f"Partner “{partner.name}” {'enabled' if partner.is_active else 'disabled'}.")
    return redirect("mobile_partners")


# ------------------------------------------------------------------ Test Verify (action prompts)
@superuser_required
def action_test(request):
    """Send an OTP / code / number prompt to a user's phone from the console — for testing the
    verification flow ourselves before a partner uses the API. No partner, no callback: the user's
    response is recorded here so we can confirm it worked."""
    if request.method == "POST":
        account = AppAccount.objects.filter(id=request.POST.get("account")).first()
        atype = request.POST.get("type")
        title = (request.POST.get("title") or "Verification").strip()
        message = (request.POST.get("message") or "").strip()
        params, ok = {}, True
        if not account:
            messages.error(request, "Choose a user.")
            ok = False
        elif atype == "otp":
            code = (request.POST.get("code") or "").strip()
            if not code:
                messages.error(request, "Enter a code for the OTP test.")
                ok = False
            else:
                params = {"code": code}
        elif atype == "code":
            try:
                length = int(request.POST.get("length") or 6)
            except (TypeError, ValueError):
                length = 6
            params = {"length": max(3, min(10, length))}
        elif atype == "number":
            nums = [n.strip() for n in (request.POST.get("numbers") or "").split(",") if n.strip()]
            if not (2 <= len(nums) <= 6):
                messages.error(request, "Enter 2–6 comma-separated numbers.")
                ok = False
            else:
                params = {"numbers": nums}
        else:
            messages.error(request, "Choose a type.")
            ok = False

        if ok:
            action = AppActionRequest.objects.create(
                partner=None, account=account, action_type=atype, title=title,
                message=message, params=params, callback_url="",
                expires_at=timezone.now() + timedelta(minutes=5),
            )
            delivered = partner_api.send_action_push(action)
            messages.success(
                request,
                f"Sent a {atype} prompt to {account.email} — {delivered} device(s). Complete it on "
                "the phone, then refresh this page to see the response below.",
            )
        return redirect("mobile_action_test")

    recent = (
        AppActionRequest.objects.select_related("account", "partner").order_by("-created_at")[:20]
    )
    return render(request, "mobileapi/action_test.html", {
        "accounts": AppAccount.objects.filter(is_active=True),
        "recent": recent,
    })


# ------------------------------------------------------------------ Devices
@superuser_required
def devices(request):
    qs = AppDevice.objects.select_related("account").all()
    return render(request, "mobileapi/devices.html", {"devices": qs})


@superuser_required
@require_POST
def device_deactivate(request, device_id):
    AppDevice.objects.filter(id=device_id).update(is_active=False)
    messages.success(request, "Device deactivated.")
    return redirect("mobile_devices")


# ------------------------------------------------------------------ Config
@superuser_required
def config(request):
    cfg = AppConfig.load()
    if request.method == "POST":
        form = AppConfigForm(request.POST, instance=cfg)
        if form.is_valid():
            form.save()
            messages.success(request, "Configuration saved.")
            return redirect("mobile_config")
        messages.error(request, form.errors.as_text())
    else:
        form = AppConfigForm(instance=cfg)
    return render(request, "mobileapi/config.html", {"form": form})


# ------------------------------------------------------------------ Remote Config
RC_KEY = "api_base_url"


@superuser_required
def remote_config(request):
    ctx = {"key": RC_KEY, "project_id": getattr(settings, "FIREBASE_PROJECT_ID", "")}

    if not fcm.is_configured():
        ctx["not_configured"] = True
        return render(request, "mobileapi/remote_config.html", ctx)

    if request.method == "POST":
        value = (request.POST.get("value") or "").strip()
        if not value:
            messages.error(request, "Enter a base URL.")
        else:
            try:
                rc.set_parameter(RC_KEY, value)
                messages.success(request, f'Remote Config "{RC_KEY}" published: {value}')
                return redirect("mobile_remote_config")
            except rc.RemoteConfigError as e:
                messages.error(request, f"Update failed: {e}")

    try:
        ctx["current"] = rc.get_parameter(RC_KEY)
    except rc.RemoteConfigError as e:
        ctx["error"] = str(e)
    return render(request, "mobileapi/remote_config.html", ctx)


# ------------------------------------------------------------------ Push
@superuser_required
def push(request):
    if request.method == "POST":
        form = PushForm(request.POST)
        if form.is_valid():
            account = form.cleaned_data["account"]
            title = form.cleaned_data["title"]
            body = form.cleaned_data["body"]
            link_url = form.cleaned_data.get("link_url")
            image_url = form.cleaned_data.get("image_url")
            data = {"link_url": link_url} if link_url else None  # tap target (opens in-app WebView)
            device_qs = AppDevice.objects.filter(is_active=True)
            if account:
                device_qs = device_qs.filter(account=account)
            tokens = list(device_qs.values_list("fcm_token", flat=True))

            # Advanced FCM (AndroidConfig) options, optionally persisted as master defaults.
            fcm_opts = form.fcm_options()
            if form.cleaned_data.get("fcm_save_defaults"):
                # event_time is a one-off timestamp — never keep it as a default.
                defaults = {k: v for k, v in fcm_opts.items() if k != "event_time"}
                cfg = AppConfig.load()
                cfg.fcm_push_defaults = defaults
                cfg.save()
            android = fcm.build_android_config(fcm_opts)

            ok = fail = 0
            if not fcm.is_configured():
                messages.warning(request, "Saved, but NOT sent — FCM is not configured.")
            elif not tokens:
                messages.warning(request, "No active devices to send to.")
            else:
                try:
                    ok, fail = fcm.send(tokens, title, body, data, image_url or None, android=android)
                    messages.success(request, f"Push sent: {ok} ok, {fail} failed.")
                except fcm.FCMError as e:
                    messages.error(request, f"Push failed: {e}")

            log_data = {}
            if link_url:
                log_data["link_url"] = link_url
            if image_url:
                log_data["image"] = image_url
            AppNotificationLog.objects.create(
                account=account, title=title, body=body, data=log_data or None,
                success_count=ok, fail_count=fail,
            )
            return redirect("mobile_push")
        messages.error(request, form.errors.as_text())
    else:
        form = PushForm()
    return render(request, "mobileapi/push.html", {
        "form": form,
        "logs": AppNotificationLog.objects.all()[:20],
        "fcm_configured": fcm.is_configured(),
        "all_urls": _distinct_link_urls(),
        "cloud_cfg": _cloudinary_widget_cfg(),
    })


def _cloudinary_widget_cfg():
    """Client-side (unsigned) Cloudinary config for the image widget. NEVER includes the secret.
    Upload uses the existing unsigned preset; pick-existing lists by the `notifications` tag."""
    return {
        "enabled": bool(getattr(settings, "CLOUDINARY_CLOUD_NAME", None)),
        "cloud_name": getattr(settings, "CLOUDINARY_CLOUD_NAME", "") or "",
        "upload_preset": getattr(settings, "CLOUDINARY_UPLOAD_PRESET", "syncup_unsigned"),
        "tag": "notifications",
        "folder": "notifications",
    }


def _distinct_link_urls():
    """All links' URLs across every account, de-duplicated by URL (first title wins).
    Used as the broadcast fallback for the 'pick an existing URL' dropdowns."""
    seen = {}
    for url, title in AppLink.objects.order_by("title").values_list("url", "title"):
        if url and url not in seen:
            seen[url] = title
    return [{"url": url, "label": f"{title} — {url}"} for url, title in seen.items()]


# ------------------------------------------------------------------ Reminders
# Reminders are pulled by the app on login / app-open / daily background sync and
# fired on-device via local alarms — no push is sent from here.
def _reminders_ctx(form, editing=None):
    reminders = (
        AppReminder.objects.select_related("account", "link")
        .annotate(
            synced_count=Count("receipts", filter=Q(receipts__synced_at__isnull=False), distinct=True),
            fired_count=Count("receipts", filter=Q(receipts__fired_at__isnull=False), distinct=True),
            last_fired=Max("receipts__fired_at"),
        )
    )
    # Cron delivery depends on an external service calling /cron/push/dispatch — if that schedule
    # dies, queued pushes silently stop going out. Surface the last run so it's visible from here.
    lock = CronLock.objects.filter(name=cron_views.PUSH_JOB).first()
    return {
        "form": form, "editing": editing, "reminders": reminders,
        "all_urls": _distinct_link_urls(), "cloud_cfg": _cloudinary_widget_cfg(),
        "cron_last_run": lock.updated_at if lock else None,
        "cron_interval_minutes": AppConfig.load().cron_dispatch_interval_minutes,
        "cron_configured": bool(getattr(settings, "CRON_KEY", None)),
        "pending_push_count": AppReminder.objects.filter(
            delivery="cron", is_active=True, status="pending",
        ).count(),
    }


_PICKUP_NOTE = "Devices pick it up on next app open or the daily sync."


@superuser_required
def reminders(request):
    if request.method == "POST":
        form = ReminderForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, f"Reminder saved. {_PICKUP_NOTE}")
            return redirect("mobile_reminders")
        messages.error(request, form.errors.as_text())
    else:
        form = ReminderForm()
    return render(request, "mobileapi/reminders.html", _reminders_ctx(form))


@superuser_required
def reminder_edit(request, reminder_id):
    reminder = get_object_or_404(AppReminder, id=reminder_id)
    if request.method == "POST":
        form = ReminderForm(request.POST, instance=reminder)
        if form.is_valid():
            form.save()
            messages.success(request, f"Reminder updated. {_PICKUP_NOTE}")
            return redirect("mobile_reminders")
        messages.error(request, form.errors.as_text())
    else:
        form = ReminderForm(instance=reminder)
    return render(request, "mobileapi/reminders.html", _reminders_ctx(form, editing=reminder))


@superuser_required
@require_POST
def reminder_delete(request, reminder_id):
    reminder = get_object_or_404(AppReminder, id=reminder_id)
    reminder.delete()
    messages.success(request, "Reminder deleted.")
    return redirect("mobile_reminders")


@superuser_required
@require_POST
def reminder_toggle(request, reminder_id):
    reminder = get_object_or_404(AppReminder, id=reminder_id)
    reminder.is_active = not reminder.is_active
    reminder.save(update_fields=["is_active", "updated_at"])
    messages.success(request, "Reminder " + ("activated." if reminder.is_active else "paused."))
    return redirect("mobile_reminders")


@superuser_required
def reminder_receipts(request, reminder_id):
    """Per-device delivery detail for one reminder — exactly which devices synced/showed it."""
    reminder = get_object_or_404(AppReminder, id=reminder_id)
    receipts = list(reminder.receipts.select_related("account").all())
    # Join device metadata (platform / app version / last seen) by (account, device_id).
    device_map = {}
    if receipts:
        device_ids = [r.device_id for r in receipts]
        for d in AppDevice.objects.filter(device_id__in=device_ids).select_related("account"):
            device_map[(d.account_id, d.device_id)] = d
    rows = []
    for r in receipts:
        d = device_map.get((r.account_id, r.device_id))
        rows.append({
            "account": r.account.email if r.account else "—",
            "device_id": r.device_id,
            "platform": d.platform if d else "",
            "app_version": d.app_version if d else "",
            "last_seen": d.last_seen if d else None,
            "synced_at": r.synced_at,
            "fired_at": r.fired_at,
        })
    return render(request, "mobileapi/reminder_receipts.html", {"reminder": reminder, "rows": rows})


# ------------------------------------------------------------------ Chats (user ↔ admin)
def _notify_chat_reply(account, body):
    """Push an admin reply to the user's devices — UNLESS they're currently on the chat screen
    (their chat polled within the last ~15s), in which case the live poll will show it."""
    if account.is_active_on_chat():
        return
    tokens = list(
        AppDevice.objects.filter(account=account, is_active=True)
        .exclude(fcm_token="").exclude(fcm_token__isnull=True)
        .values_list("fcm_token", flat=True)
    )
    if tokens and fcm.is_configured():
        try:
            fcm.send(tokens, "New message from Admin", body[:120], data={"type": "chat"})
        except Exception:
            pass  # message is saved regardless; the app picks it up on next poll/open


@superuser_required
def chats(request):
    """List every account that has a chat thread, newest activity first, with unread counts."""
    accounts = (
        AppAccount.objects.filter(chat_messages__isnull=False).distinct()
        .annotate(
            last_message_at=Max("chat_messages__created_at"),
            unread=Count(
                "chat_messages",
                filter=Q(chat_messages__sender="user", chat_messages__read_by_admin=False),
            ),
        )
        .order_by("-last_message_at")
    )
    return render(request, "mobileapi/chats.html", {"accounts": accounts})


@superuser_required
def chat_detail(request, account_id):
    """View a single account's thread and reply. A reply pushes an FCM nudge to their devices."""
    account = get_object_or_404(AppAccount, id=account_id)
    if request.method == "POST":
        body = (request.POST.get("body") or "").strip()
        if body:
            AppChatMessage.objects.create(
                account=account, sender="admin", body=body[:4000],
                read_by_admin=True, read_by_user=False,
            )
            _notify_chat_reply(account, body)
            messages.success(request, "Reply sent.")
        return redirect("mobile_chat_detail", account_id=account.id)
    # Opening the thread marks the user's messages as read by admin.
    AppChatMessage.objects.filter(account=account, sender="user", read_by_admin=False).update(read_by_admin=True)
    return render(request, "mobileapi/chat_detail.html", {
        "account": account,
        "msgs": account.chat_messages.all(),
    })


# --- JSON endpoints powering the live inbox (chats.html) ---
@superuser_required
def chat_list_json(request):
    """Conversation list: accounts with a thread, newest first, with unread count + last preview."""
    accounts = (
        AppAccount.objects.filter(chat_messages__isnull=False).distinct()
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
            "id": a.id,
            "name": a.name,
            "email": a.email,
            "unread": a.unread,
            "last_body": (last.body[:90] if last else ""),
            "last_sender": (last.sender if last else ""),
            "last_at_ms": int(a.last_message_at.timestamp() * 1000) if a.last_message_at else 0,
            # Teams-style presence (based on the user's chat polling):
            "online": a.is_active_on_chat(30),
            "last_seen_ms": int(a.chat_last_seen_at.timestamp() * 1000) if a.chat_last_seen_at else 0,
        })
    return JsonResponse({"conversations": data})


@superuser_required
def chat_thread_json(request, account_id):
    """Messages for a thread (optionally since a given id for polling). Marks user msgs read."""
    account = get_object_or_404(AppAccount, id=account_id)
    qs = account.chat_messages.all()
    since = request.GET.get("since")
    if since and since.isdigit():
        qs = qs.filter(id__gt=int(since))
    rows = [chat_message_dict(m) for m in qs]
    AppChatMessage.objects.filter(account=account, sender="user", read_by_admin=False).update(read_by_admin=True)
    # The admin is viewing this thread → stamp presence (shows the user an "admin active" dot).
    AppAccount.objects.filter(pk=account.pk).update(admin_last_seen_at=timezone.now())
    # Highest admin message the user has already read → drives read receipts (✓ vs ✓✓).
    read_upto = (
        AppChatMessage.objects.filter(account=account, sender="admin", read_by_user=True)
        .aggregate(m=Max("id"))["m"] or 0
    )
    return JsonResponse({
        "account": {
            "id": account.id, "name": account.name, "email": account.email,
            "active": account.is_active,
            "online": account.is_active_on_chat(30),
            "last_seen_ms": int(account.chat_last_seen_at.timestamp() * 1000) if account.chat_last_seen_at else 0,
        },
        "messages": rows,
        "read_upto_id": read_upto,
        "user_typing": account.is_typing(),
    })


@superuser_required
@require_POST
def chat_reply_json(request, account_id):
    """AJAX reply: create an admin message, push FCM, return the new message."""
    account = get_object_or_404(AppAccount, id=account_id)
    try:
        body = (json.loads(request.body or b"{}").get("body") or "").strip()
    except (ValueError, TypeError):
        body = ""
    if not body:
        return JsonResponse({"error": "empty"}, status=400)
    msg = AppChatMessage.objects.create(
        account=account, sender="admin", body=body[:4000], read_by_admin=True, read_by_user=False,
    )
    _notify_chat_reply(account, body)
    return JsonResponse({"ok": True, "message": chat_message_dict(msg)})


@superuser_required
@require_POST
def chat_typing_admin(request, account_id):
    """Admin is typing a reply — set a short-lived flag the user's app shows as 'typing…'."""
    AppAccount.objects.filter(id=account_id).update(chat_admin_typing_until=timezone.now() + timedelta(seconds=6))
    return JsonResponse({"ok": True})
