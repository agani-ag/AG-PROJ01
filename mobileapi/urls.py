"""Routes for the SyncUp mobile API — mounted under /app/v1/ (see main/urls.py)."""
from django.urls import path

from . import views

urlpatterns = [
    path("auth/login", views.login, name="app_login"),
    path("auth/logout", views.logout, name="app_logout"),
    path("account/urls", views.account_urls, name="app_account_urls"),
    path("account/links", views.account_link_add, name="app_account_link_add"),
    path("account/links/<int:link_id>/delete", views.account_link_delete, name="app_account_link_delete"),
    path("account/change-password", views.change_password, name="app_change_password"),
    path("account/delete", views.delete_account, name="app_account_delete"),
    path("devices/register", views.device_register, name="app_device_register"),
    path("devices/unregister", views.device_unregister, name="app_device_unregister"),
    path("reminders", views.reminders, name="app_reminders"),
    path("reminders/ack", views.reminder_ack, name="app_reminder_ack"),
    path("chat/session", views.chat_session, name="app_chat_session"),
    path("chat/unread", views.chat_unread, name="app_chat_unread"),
    path("config", views.config, name="app_config"),
]
