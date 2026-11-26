"""Routes for the SyncUp mobile API — mounted under /app/v1/ (see main/urls.py)."""
from django.urls import path

from . import views
from . import radio
from . import account_views
from . import browser_sync
from . import dns_services
from . import home_shortcuts
from . import notifications_api
from . import hub_links
from . import hub_people
from . import cloud_api
from . import cloud_notify

urlpatterns = [
    path("auth/login", views.login, name="app_login"),
    path("auth/logout", views.logout, name="app_logout"),
    path("auth/signup", account_views.signup, name="app_signup"),
    path("account/profile", account_views.profile_update, name="app_profile_update"),
    path("account/username-check", account_views.username_check, name="app_username_check"),
    path("account/partners", account_views.partners, name="app_partners"),
    path("account/partners/<int:connection_id>/enable", account_views.partner_enable, name="app_partner_enable"),
    path("account/partners/<int:connection_id>/disable", account_views.partner_disable, name="app_partner_disable"),
    path("device/hello", account_views.device_hello, name="app_device_hello"),
    path("device/beat", account_views.device_beat, name="app_device_beat"),
    path("browser/sync", browser_sync.browser_sync, name="app_browser_sync"),
    path("browser/sync/delete", browser_sync.browser_sync_delete, name="app_browser_sync_delete"),
    path("radio/channels", radio.channels, name="app_radio_channels"),
    path("account/urls", views.account_urls, name="app_account_urls"),
    path("account/me", views.account_me, name="app_account_me"),
    path("sync", views.sync, name="app_sync"),
    # Notifications page (New / Read) — signed in, or signed out by device id + secret.
    path("notifications", notifications_api.notifications, name="app_notifications"),
    path("notifications/read", notifications_api.notifications_read, name="app_notifications_read"),
    path("notifications/count", notifications_api.notifications_count, name="app_notifications_count"),
    path("account/links", views.account_link_add, name="app_account_link_add"),
    path("account/links/<int:link_id>/delete", views.account_link_delete, name="app_account_link_delete"),
    path("partner/notify", views.partner_notify, name="app_partner_notify"),
    path("account/change-password", views.change_password, name="app_change_password"),
    path("account/delete", views.delete_account, name="app_account_delete"),
    path("devices/register", views.device_register, name="app_device_register"),
    path("devices/unregister", views.device_unregister, name="app_device_unregister"),
    path("reminders", views.reminders, name="app_reminders"),
    path("reminders/ack", views.reminder_ack, name="app_reminder_ack"),
    path("chat/session", views.chat_session, name="app_chat_session"),
    path("chat/unread", views.chat_unread, name="app_chat_unread"),
    path("config", views.config, name="app_config"),
    path("shortcuts", home_shortcuts.catalog, name="app_shortcuts"),  # home page catalogue, no sign-in
    path("dns", dns_services.catalog, name="app_dns"),  # DNS services for browsing, no sign-in
    path("general-links", views.general_links, name="app_general_links"),  # SyncUp links for everyone, no sign-in
    path("hub/links", hub_links.hub_links, name="app_hub_links"),
    path("hub/people", hub_people.people, name="app_hub_people"),  # SyncHub copies SyncUp's people (links key)
    path("hub/people/who", hub_people.who, name="app_hub_people_who"),
    # Partner action / verification prompts shown on the phone
    # App → Tools → Cloudinary (cloud_api.py): the server signs, the phone talks to Cloudinary itself.
    path("cloud/access", cloud_api.access, name="app_cloud_access"),
    path("cloud/credentials", cloud_api.credentials, name="app_cloud_credentials"),
    path("cloud/files", cloud_api.files, name="app_cloud_files"),
    path("cloud/sign", cloud_api.sign_request, name="app_cloud_sign"),
    path("cloud/uploaded", cloud_api.uploaded, name="app_cloud_uploaded"),
    path("cloud/deleted", cloud_api.deleted, name="app_cloud_deleted"),
    path("cloud/renamed", cloud_api.renamed, name="app_cloud_renamed"),
    path("cloud/folders", cloud_api.folder_create, name="app_cloud_folder_create"),
    path("cloud/folders/delete", cloud_api.folder_delete, name="app_cloud_folder_delete"),
    path("cloud/accounts", cloud_api.accounts, name="app_cloud_accounts"),
    path("cloud/pending", cloud_api.pending, name="app_cloud_pending"),
    path("cloud/usage", cloud_api.usage, name="app_cloud_usage"),
    path("cloud/stats", cloud_api.stats, name="app_cloud_stats"),
    # Cloudinary calls this on every upload / move / rename / delete (Console → Settings → Webhook Notifications).
    path("cloud/notify", cloud_notify.notify, name="app_cloud_notify"),
    path("action/<int:action_id>", views.action_get, name="app_action_get"),
    path("action/<int:action_id>/respond", views.action_respond, name="app_action_respond"),
]
