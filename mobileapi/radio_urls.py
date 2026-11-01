"""AudioSync's presence route — mounted at the root (see main/urls.py).

Open (see radio.py); stations POST their presence events here.
"""
from django.urls import path

from . import radio

urlpatterns = [
    path("station/presence", radio.ingest, name="station_presence"),
]
