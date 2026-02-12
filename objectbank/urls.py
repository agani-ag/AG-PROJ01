from django.urls import path
from .views import (
    auth, views, link_registry,
    profile
    
)

urlpatterns = [
    # Home URL
    path('', views.home, name='home'),

    # Auth URLs
    path('login', auth.login_view, name='login'),
    path('signup', auth.signup_view, name='signup'),
    path('logout', auth.logout_view, name='logout'),

    # Profile URL
    path('profiles', profile.profiles, name='profiles'),
    path('profile/edit/', profile.profile_edit, name='profile-edit'),
    path('profile/delete/<int:user_id>/', profile.profile_delete, name='profile-delete'),
    path('profile/admin/edit/<int:user_id>/', profile.admin_profile_edit, name='profile-admin-edit'),

    # Link Registry URL
    path('link-registry/', link_registry.link_registry_view, name='link-registry'),
]