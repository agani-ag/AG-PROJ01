"""HTML admin routes for the SyncUp mobile API — mounted at /mobile/ (superuser-only)."""
from django.urls import path

from . import admin_insights as insights
from . import admin_shortcuts as sc
from . import admin_views as v

urlpatterns = [
    path("", v.dashboard, name="mobile_dashboard"),

    # Accounts
    path("accounts", v.accounts, name="mobile_accounts"),
    path("accounts/add", v.account_add, name="mobile_account_add"),
    path("accounts/<int:account_id>", v.account_edit, name="mobile_account_edit"),
    path("accounts/<int:account_id>/delete", v.account_delete, name="mobile_account_delete"),
    path("accounts/<int:account_id>/signout", v.account_signout, name="mobile_account_signout"),
    path("accounts/<int:account_id>/reset", v.account_reset, name="mobile_account_reset"),

    # Links
    path("links/add", v.link_add, name="mobile_link_add"),
    path("links/<int:link_id>/edit", v.link_edit, name="mobile_link_edit"),
    path("links/<int:link_id>/delete", v.link_delete, name="mobile_link_delete"),

    # General links (shown to all users)
    path("general-links", v.general_links, name="mobile_general_links"),
    path("general-links/add", v.general_link_add, name="mobile_general_link_add"),
    path("general-links/<int:link_id>/edit", v.general_link_edit, name="mobile_general_link_edit"),

    # Verification test tool (send OTP / code / number prompts to a user)
    # Home shortcuts: the app's home page catalogue (for everyone)
    path("home-shortcuts", sc.home_shortcuts, name="mobile_home_shortcuts"),
    path("home-shortcuts/add", sc.shortcut_add, name="mobile_home_shortcut_add"),
    path("home-shortcuts/<int:shortcut_id>/edit", sc.shortcut_edit, name="mobile_home_shortcut_edit"),
    path("home-shortcuts/<int:shortcut_id>/toggle", sc.shortcut_toggle, name="mobile_home_shortcut_toggle"),
    path("home-shortcuts/<int:shortcut_id>/delete", sc.shortcut_delete, name="mobile_home_shortcut_delete"),
    path("home-shortcuts/categories/add", sc.category_add, name="mobile_shortcut_category_add"),
    path("home-shortcuts/categories/<int:category_id>/rename", sc.category_rename, name="mobile_shortcut_category_rename"),
    path("home-shortcuts/categories/<int:category_id>/toggle", sc.category_toggle, name="mobile_shortcut_category_toggle"),
    path("home-shortcuts/categories/<int:category_id>/delete", sc.category_delete, name="mobile_shortcut_category_delete"),
    path("home-shortcuts/reorder", sc.reorder, name="mobile_home_shortcuts_reorder"),
    path("home-shortcuts/bulk", sc.bulk_add, name="mobile_home_shortcuts_bulk"),

    path("verify-test", v.action_test, name="mobile_action_test"),

    # Partners (B2B provisioning API)
    path("partners", v.partners, name="mobile_partners"),
    path("partners/<int:partner_id>/regenerate", v.partner_regenerate, name="mobile_partner_regenerate"),
    path("partners/<int:partner_id>/toggle", v.partner_toggle, name="mobile_partner_toggle"),

    # Telegram (our bot config, onboarding kit, send test, logs)
    path("telegram", v.telegram_page, name="mobile_telegram"),
    path("telegram/save", v.telegram_save, name="mobile_telegram_save"),
    path("telegram/verify", v.telegram_verify, name="mobile_telegram_verify"),
    path("telegram/discover", v.telegram_discover, name="mobile_telegram_discover"),
    path("telegram/test", v.telegram_test, name="mobile_telegram_test"),
    # Which Telegram chats each partner may send to
    path("telegram/partner/<int:partner_id>/chats/add", v.telegram_partner_chat_add, name="mobile_telegram_chat_add"),
    path("telegram/chats/<int:chat_pk>/delete", v.telegram_partner_chat_delete, name="mobile_telegram_chat_delete"),
    path("telegram/partner/<int:partner_id>/allow-any", v.telegram_partner_allow_any, name="mobile_telegram_allow_any"),
    # The registry of chats our bot is in
    path("telegram/chats/refresh", v.telegram_chats_refresh, name="mobile_telegram_chats_refresh"),
    path("telegram/chats/recheck", v.telegram_chats_recheck, name="mobile_telegram_chats_recheck"),
    path("telegram/chats/<int:chat_pk>/refresh", v.telegram_chat_refresh_one, name="mobile_telegram_chat_refresh"),
    path("telegram/chats/<int:chat_pk>/assign", v.telegram_chat_assign, name="mobile_telegram_chat_assign"),
    path("telegram/chats/<int:chat_pk>/forget", v.telegram_chat_forget, name="mobile_telegram_chat_forget"),
    # Telegram inbox: conversations with groups and people
    path("telegram/inbox", v.telegram_inbox, name="mobile_telegram_inbox"),
    path("telegram/inbox/list.json", v.telegram_inbox_list, name="mobile_telegram_inbox_list"),
    path("telegram/inbox/<int:chat_pk>/messages.json", v.telegram_inbox_thread, name="mobile_telegram_inbox_thread"),
    path("telegram/inbox/<int:chat_pk>/send", v.telegram_inbox_send, name="mobile_telegram_inbox_send"),
    path("telegram/live", v.telegram_live_toggle, name="mobile_telegram_live"),

    # Radio (live channel registry + AudioSync ingest key)
    path("radio", v.radio_page, name="mobile_radio"),
    path("radio/save", v.radio_save, name="mobile_radio_save"),
    path("radio/channels/<int:channel_id>/remove", v.radio_channel_remove, name="mobile_radio_channel_remove"),

    # Devices
    path("devices", v.devices, name="mobile_devices"),
    path("devices/<int:device_pk>", insights.device_detail, name="mobile_device_detail"),
    path("devices/<int:device_id>/deactivate", v.device_deactivate, name="mobile_device_deactivate"),

    # Config + Push + Remote Config
    path("config", v.config, name="mobile_config"),
    path("remote-config", v.remote_config, name="mobile_remote_config"),
    path("push", v.push, name="mobile_push"),
    path("push-log", v.push_log, name="mobile_push_log"),

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
