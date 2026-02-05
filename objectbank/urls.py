from django.urls import path
from .views import (
    auth, views, link_registry
)

urlpatterns = [
    # Home URL
    path('', views.home, name='home'),

    # Auth URLs
    path('login', auth.login_view, name='login'),
    path('signup', auth.signup_view, name='signup'),
    path('logout', auth.logout_view, name='logout'),

    # Link Registry URL
    path('link-registry/', link_registry.link_registry_view, name='link-registry'),
]