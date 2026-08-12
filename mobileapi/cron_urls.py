"""Routes for the HTTP-triggered cron endpoints — mounted at /cron/ (see main/urls.py).

Deliberately outside /app/v1/ (bearer-token mobile auth) and /mobile/ (superuser session auth):
these are called by an external cron service and carry their own shared-secret gate.
"""
from django.urls import path

from . import cron_views

urlpatterns = [
    path("health", cron_views.health, name="cron_health"),
    path("push/dispatch", cron_views.dispatch_push, name="cron_push_dispatch"),
    path("cleanup", cron_views.cleanup, name="cron_cleanup"),
]
