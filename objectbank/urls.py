from django.urls import path
from .views import (
    auth, views, link_registry,
    profile
    
)

urlpatterns = [
    # Home URL
    path('', views.dashboard_view, name='home'),

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

    # Dashboard URL
    path("dashboard/", views.dashboard_view, name="dashboard"),

    # Project URLs
    path("projects/", views.project_list_view, name="project_list"),
    path("projects/create/", views.project_create_view, name="project_create"),
    path("projects/<int:pk>/", views.project_detail_view, name="project_detail"),

    # Worker URLs
    path("workers/", views.worker_list_view, name="worker_list"),
    path("workers/create/", views.worker_create_view, name="worker_create"),
    path("workers/<int:pk>/", views.worker_detail_view, name="worker_detail"),
    path("workers/top-performers/", views.top_performers_view, name="top_performers"),

    # Revenue URLs
    path("revenue/create/", views.revenue_create_view, name="revenue_create"),

    # Requirements URLs
    path("requirements/create/", views.requirement_create_view, name="requirement_create"),
    
    # Analytics URLs
    path("analytics/dashboard/", views.analytics_dashboard_view, name="analytics_dashboard"),
    path("analytics/pareto/", views.pareto_analysis_view, name="pareto_analysis"),
    
    # Risk & Credit URLs
    path("risk/dashboard/", views.risk_dashboard_view, name="risk_dashboard"),
    
    # Assignment Intelligence URLs
    path("assignments/overview/", views.assignments_overview_view, name="assignments_overview"),
    path("assignments/intelligence/<int:requirement_id>/", views.assignment_intelligence_view, name="assignment_intelligence"),
]
