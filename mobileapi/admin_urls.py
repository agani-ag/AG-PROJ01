"""HTML admin routes for the SyncUp mobile API — mounted at /mobile/ (superuser-only)."""
from django.urls import path

from . import admin_views as v

urlpatterns = [
    path("", v.dashboard, name="mobile_dashboard"),

    # Accounts
    path("accounts", v.accounts, name="mobile_accounts"),
    path("accounts/add", v.account_add, name="mobile_account_add"),
    path("accounts/<int:account_id>", v.account_edit, name="mobile_account_edit"),
    path("accounts/<int:account_id>/delete", v.account_delete, name="mobile_account_delete"),

    # Links
    path("links/add", v.link_add, name="mobile_link_add"),
    path("links/<int:link_id>/edit", v.link_edit, name="mobile_link_edit"),
    path("links/<int:link_id>/delete", v.link_delete, name="mobile_link_delete"),

    # General links (shown to all users)
    path("general-links", v.general_links, name="mobile_general_links"),
    path("general-links/add", v.general_link_add, name="mobile_general_link_add"),
    path("general-links/<int:link_id>/edit", v.general_link_edit, name="mobile_general_link_edit"),

    # Verification test tool (send OTP / code / number prompts to a user)
    path("verify-test", v.action_test, name="mobile_action_test"),

    # Partners (B2B provisioning API)
    path("partners", v.partners, name="mobile_partners"),
    path("partners/<int:partner_id>/regenerate", v.partner_regenerate, name="mobile_partner_regenerate"),
    path("partners/<int:partner_id>/toggle", v.partner_toggle, name="mobile_partner_toggle"),

    # Devices
    path("devices", v.devices, name="mobile_devices"),
    path("devices/<int:device_id>/deactivate", v.device_deactivate, name="mobile_device_deactivate"),

    # Config + Push + Remote Config
    path("config", v.config, name="mobile_config"),
    path("remote-config", v.remote_config, name="mobile_remote_config"),
    path("push", v.push, name="mobile_push"),

    # Reminders (device-fired)
    path("reminders", v.reminders, name="mobile_reminders"),
    path("reminders/<int:reminder_id>/edit", v.reminder_edit, name="mobile_reminder_edit"),
    path("reminders/<int:reminder_id>/delete", v.reminder_delete, name="mobile_reminder_delete"),
    path("reminders/<int:reminder_id>/toggle", v.reminder_toggle, name="mobile_reminder_toggle"),
    path("reminders/<int:reminder_id>/receipts", v.reminder_receipts, name="mobile_reminder_receipts"),

    # Chats (user ↔ admin)
    path("chats", v.chats, name="mobile_chats"),
    path("chats/list.json", v.chat_list_json, name="mobile_chat_list_json"),
    path("chats/<int:account_id>/messages.json", v.chat_thread_json, name="mobile_chat_thread_json"),
    path("chats/<int:account_id>/reply", v.chat_reply_json, name="mobile_chat_reply_json"),
    path("chats/<int:account_id>/typing", v.chat_typing_admin, name="mobile_chat_typing"),
    path("chats/<int:account_id>", v.chat_detail, name="mobile_chat_detail"),
]
