from django.urls import path
from .views import (
    auth, views
)

urlpatterns = [
    path('', views.home, name='home'),
    path('signup', auth.signup_view, name='signup'),
]