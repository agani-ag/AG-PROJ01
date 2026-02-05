# objectbank/urls.py
from django.urls import path
from .views import (
    public_api, items_api
)

urlpatterns = [
    path('', public_api, name='public-api'),
    path('items/', items_api, name='items-api'),
]
