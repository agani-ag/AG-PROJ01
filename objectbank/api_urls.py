from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    auth, views, link_registry
)

router = DefaultRouter()
router.register(r'links', link_registry.LinkRegistryViewSet, basename='links')

urlpatterns = [
    # function-based or class-based non-viewset APIs
    path('', views.public_api, name='public-api'),
    path('items/', views.items_api, name='items-api'),

    # DRF router URLs
    path('', include(router.urls)),
]
