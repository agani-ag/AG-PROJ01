"""
Custom HTML admin screens for the SyncUp mobile API (superuser-only).

Function-based views + Bootstrap templates, matching the existing syncup app.
Mounted at /mobile/ (see admin_urls.py). Separate from the JSON API (/app/v1/).
"""
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import fcm
from . import remoteconfig as rc
from .forms import AppAccountForm, AppConfigForm, AppLinkForm, PushForm, ReminderForm
from .models import (
    AppAccount,
    AppConfig,
    AppDevice,
    AppLink,
    AppNotificationLog,
    AppReminder,
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
    }
    return render(request, "mobileapi/dashboard.html", ctx)


# ------------------------------------------------------------------ Accounts
@superuser_required
def accounts(request):
    qs = AppAccount.objects.annotate(
        link_count=Count("links", distinct=True),
        device_count=Count("devices", distinct=True),
    )
    return render(request, "mobileapi/accounts.html", {"accounts": qs})


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
    return render(request, "mobileapi/link_edit.html", {"form": form, "account": account})


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
    })


@superuser_required
@require_POST
def link_delete(request, link_id):
    link = get_object_or_404(AppLink, id=link_id)
    account_id = link.account_id
    link.delete()
    messages.success(request, "Link deleted.")
    return redirect("mobile_account_edit", account_id)


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
            device_qs = AppDevice.objects.filter(is_active=True)
            if account:
                device_qs = device_qs.filter(account=account)
            tokens = list(device_qs.values_list("fcm_token", flat=True))

            ok = fail = 0
            if not fcm.is_configured():
                messages.warning(request, "Saved, but NOT sent — FCM is not configured.")
            elif not tokens:
                messages.warning(request, "No active devices to send to.")
            else:
                try:
                    ok, fail = fcm.send(tokens, title, body)
                    messages.success(request, f"Push sent: {ok} ok, {fail} failed.")
                except fcm.FCMError as e:
                    messages.error(request, f"Push failed: {e}")

            AppNotificationLog.objects.create(
                account=account, title=title, body=body,
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
    })


# ------------------------------------------------------------------ Reminders
# Reminders are pulled by the app on login / app-open / daily background sync and
# fired on-device via local alarms — no push is sent from here.
def _reminders_ctx(form, editing=None):
    return {
        "form": form,
        "editing": editing,
        "reminders": AppReminder.objects.select_related("account", "link").all(),
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
