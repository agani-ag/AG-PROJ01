"""
Admin-only management UI for the SyncUp mobile API.

Login is the standard Django admin (/admin/) with a staff/superuser `User`.
These screens manage the separate AppAccount world; see the plan §6.
"""
from django import forms
from django.contrib import admin, messages

from . import fcm
from .models import (
    AppAccount,
    AppAuthToken,
    AppConfig,
    AppDevice,
    AppLink,
    AppNotificationLog,
)


# ----------------------------- Accounts + links ---------------------------- #
class AppAccountForm(forms.ModelForm):
    new_password = forms.CharField(
        label="Set / reset password",
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Enter a value to set (new account) or reset the password.",
    )

    class Meta:
        model = AppAccount
        fields = ["name", "email", "is_active"]

    def clean_new_password(self):
        pw = self.cleaned_data.get("new_password")
        if not self.instance.pk and not pw:
            raise forms.ValidationError("A password is required for a new account.")
        if pw and len(pw) < 6:
            raise forms.ValidationError("Password must be at least 6 characters.")
        return pw

    def save(self, commit=True):
        account = super().save(commit=False)
        raw = self.cleaned_data.get("new_password")
        if raw:
            account.set_password(raw)
        if commit:
            account.save()
        return account


class AppLinkInline(admin.TabularInline):
    model = AppLink
    extra = 1
    fields = ["title", "url", "description", "icon", "sort_order", "is_active"]


@admin.register(AppAccount)
class AppAccountAdmin(admin.ModelAdmin):
    form = AppAccountForm
    list_display = ["name", "email", "is_active", "last_login", "created_at"]
    list_filter = ["is_active"]
    search_fields = ["name", "email"]
    readonly_fields = ["last_login", "created_at", "updated_at"]
    inlines = [AppLinkInline]
    actions = ["send_test_push", "deactivate_accounts", "activate_accounts"]

    @admin.action(description="Send a test push to selected accounts")
    def send_test_push(self, request, queryset):
        if not fcm.is_configured():
            self.message_user(
                request,
                "FCM is not configured (set SYNCUP_FIREBASE_PROJECT_ID and "
                "SYNCUP_FIREBASE_SA_FILE).",
                level=messages.WARNING,
            )
            return
        tokens = list(
            AppDevice.objects.filter(account__in=queryset, is_active=True)
            .values_list("fcm_token", flat=True)
        )
        try:
            ok, fail = fcm.send(tokens, "SyncUp", "This is a test notification.")
            AppNotificationLog.objects.create(
                title="SyncUp", body="This is a test notification.",
                success_count=ok, fail_count=fail,
            )
            self.message_user(request, f"Push sent: {ok} ok, {fail} failed.")
        except fcm.FCMError as e:
            self.message_user(request, f"Push failed: {e}", level=messages.ERROR)

    @admin.action(description="Deactivate selected accounts")
    def deactivate_accounts(self, request, queryset):
        n = queryset.update(is_active=False)
        self.message_user(request, f"{n} account(s) deactivated.")

    @admin.action(description="Activate selected accounts")
    def activate_accounts(self, request, queryset):
        n = queryset.update(is_active=True)
        self.message_user(request, f"{n} account(s) activated.")


@admin.register(AppLink)
class AppLinkAdmin(admin.ModelAdmin):
    list_display = ["title", "account", "url", "sort_order", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["title", "url", "account__email"]
    list_editable = ["sort_order", "is_active"]


# ----------------------------- Devices ------------------------------------- #
@admin.register(AppDevice)
class AppDeviceAdmin(admin.ModelAdmin):
    list_display = ["account", "platform", "app_version", "last_seen", "is_active"]
    list_filter = ["platform", "is_active"]
    search_fields = ["account__email", "device_id"]
    readonly_fields = ["account", "device_id", "fcm_token", "platform", "app_version", "last_seen"]
    actions = ["deactivate_devices"]

    def has_add_permission(self, request):
        return False  # devices self-register via the API

    @admin.action(description="Deactivate selected devices")
    def deactivate_devices(self, request, queryset):
        n = queryset.update(is_active=False)
        self.message_user(request, f"{n} device(s) deactivated.")


# ----------------------------- Tokens (read-only) -------------------------- #
@admin.register(AppAuthToken)
class AppAuthTokenAdmin(admin.ModelAdmin):
    list_display = ["account", "key", "created_at", "last_used_at", "revoked"]
    list_filter = ["revoked"]
    search_fields = ["account__email", "key"]
    readonly_fields = ["account", "key", "created_at", "last_used_at", "expires_at"]
    actions = ["revoke_tokens"]

    def has_add_permission(self, request):
        return False

    @admin.action(description="Revoke selected tokens")
    def revoke_tokens(self, request, queryset):
        n = queryset.update(revoked=True)
        self.message_user(request, f"{n} token(s) revoked.")


# ----------------------------- Config (singleton) -------------------------- #
@admin.register(AppConfig)
class AppConfigAdmin(admin.ModelAdmin):
    list_display = ["__str__", "min_supported_version", "latest_version", "announcement_active", "updated_at"]

    def has_add_permission(self, request):
        return not AppConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


# ----------------------------- Push composer / log ------------------------- #
@admin.register(AppNotificationLog)
class AppNotificationLogAdmin(admin.ModelAdmin):
    list_display = ["title", "account", "sent_at", "success_count", "fail_count"]
    search_fields = ["title", "body", "account__email"]
    readonly_fields = ["sent_at", "success_count", "fail_count"]

    def get_fields(self, request, obj=None):
        if obj is None:  # compose screen
            return ["account", "title", "body"]
        return ["account", "title", "body", "sent_at", "success_count", "fail_count"]

    def save_model(self, request, obj, form, change):
        # Only send on creation; editing an existing log never re-sends.
        if change:
            super().save_model(request, obj, form, change)
            return
        if not fcm.is_configured():
            obj.success_count = 0
            obj.fail_count = 0
            super().save_model(request, obj, form, change)
            self.message_user(
                request,
                "Saved, but NOT sent — FCM is not configured "
                "(set SYNCUP_FIREBASE_PROJECT_ID and SYNCUP_FIREBASE_SA_FILE).",
                level=messages.WARNING,
            )
            return

        devices = AppDevice.objects.filter(is_active=True)
        if obj.account:
            devices = devices.filter(account=obj.account)
        tokens = list(devices.values_list("fcm_token", flat=True))
        try:
            ok, fail = fcm.send(tokens, obj.title, obj.body, obj.data or None)
            obj.success_count = ok
            obj.fail_count = fail
            super().save_model(request, obj, form, change)
            self.message_user(request, f"Push sent: {ok} ok, {fail} failed.")
        except fcm.FCMError as e:
            obj.success_count = 0
            obj.fail_count = 0
            super().save_model(request, obj, form, change)
            self.message_user(request, f"Saved, but push failed: {e}", level=messages.ERROR)
