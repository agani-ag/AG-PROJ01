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

    # Devices
    path("devices", v.devices, name="mobile_devices"),
    path("devices/<int:device_id>/deactivate", v.device_deactivate, name="mobile_device_deactivate"),

    # Config + Push + Remote Config
    path("config", v.config, name="mobile_config"),
    path("remote-config", v.remote_config, name="mobile_remote_config"),
    path("push", v.push, name="mobile_push"),
]
