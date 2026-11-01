"""The Channels list's private link, public by design (see channel_lists.py) — mounted at the site root."""
from django.urls import path

from . import channel_lists

urlpatterns = [
    path("channels/<str:token>.m3u", channel_lists.list_m3u, name="channel_list_m3u"),
]
