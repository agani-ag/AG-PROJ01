from django.urls import path
from .views import (
    auth, views
)

urlpatterns = [
    # Home URL
    path('', views.home, name='home'),

    # Auth URLs
    path('login', auth.login_view, name='login'),
    path('signup', auth.signup_view, name='signup'),
    path('logout', auth.logout_view, name='logout'),
]