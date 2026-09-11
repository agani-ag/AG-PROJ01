"""Routes for the Partner provisioning API — mounted at /partner/v1/ (see main/urls.py).

Key-authenticated (Authorization: Bearer <partner key>), scoped to the partner's own users/links.
Users are addressable by our internal id (/users/<id>) or the partner's own id
(/users/external/<external_id>); the latter also supports PUT for an idempotent upsert.
"""
from django.urls import path

from . import partner_api as v

urlpatterns = [
    # Users
    path("users", v.users, name="partner_users"),
    path("users/bulk", v.users_bulk, name="partner_users_bulk"),
    path("users/<int:user_id>", v.user_by_id, name="partner_user_by_id"),
    path("users/external/<str:external_id>", v.user_by_external, name="partner_user_by_external"),

    # Links (address the user by internal id or external id)
    path("users/<int:user_id>/links", v.links_by_user_id, name="partner_links_by_id"),
    path("users/external/<str:external_id>/links", v.links_by_user_external, name="partner_links_by_external"),
    path("links/<int:link_id>", v.link_detail, name="partner_link_detail"),

    # Notifications (push to the partner's own users)
    path("users/<int:user_id>/notify", v.notify_user_by_id, name="partner_notify_by_id"),
    path("users/external/<str:external_id>/notify", v.notify_user_by_external, name="partner_notify_by_external"),
    path("notify", v.notify_all, name="partner_notify_all"),
    path("notify/bulk", v.notify_bulk, name="partner_notify_bulk"),

    # Action / verification prompts (otp / code / number / notice / approve)
    path("users/<int:user_id>/action", v.action_by_id, name="partner_action_by_id"),
    path("users/external/<str:external_id>/action", v.action_by_external, name="partner_action_by_external"),
    # Poll a prompt's answer after the fact (callback safety net).
    path("actions/<int:request_id>", v.action_status, name="partner_action_status"),
]
