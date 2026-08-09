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
from .forms import AppAccountForm, AppConfigForm, AppLinkForm, PushForm
from .models import AppAccount, AppConfig, AppDevice, AppLink, AppNotificationLog

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
