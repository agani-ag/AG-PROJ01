# objectbank/urls.py
from django.urls import path
from .views import (
    views
)

urlpatterns = [
    path('', views.public_api, name='public-api'),
    path('items/', views.items_api, name='items-api'),
]
