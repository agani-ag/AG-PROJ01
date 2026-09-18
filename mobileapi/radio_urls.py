"""Radio ingest route — mounted at /radio/ (see main/urls.py).

Bearer-key authenticated; AudioSync broadcasters POST their presence events here.
"""
from django.urls import path

from . import radio

urlpatterns = [
    path("ingest", radio.ingest, name="radio_ingest"),
]
