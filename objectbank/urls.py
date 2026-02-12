from django.urls import path
from .views import (
    auth, views, link_registry,
    profile, leads_engine
    
)

urlpatterns = [
    # Home URL
    path('', leads_engine.dashboard_view, name='home'),

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
    path("dashboard/", leads_engine.dashboard_view, name="dashboard"),

    # Project URLs
    path("projects/", leads_engine.project_list_view, name="project_list"),
    path("projects/create/", leads_engine.project_create_view, name="project_create"),
    path("projects/<int:pk>/", leads_engine.project_detail_view, name="project_detail"),
    
    # Project Lifecycle URLs
    path("projects/<int:project_pk>/assign-worker/", leads_engine.assign_worker_to_project_view, name="assign_worker_to_project"),
    path("projects/<int:project_pk>/remove-worker/<int:worker_project_pk>/", leads_engine.remove_worker_from_project_view, name="remove_worker_from_project"),
    path("projects/<int:project_pk>/update-status/", leads_engine.update_lead_status_view, name="update_lead_status"),
    path("projects/<int:project_pk>/update-stage/", leads_engine.update_construction_stage_view, name="update_construction_stage"),
    path("projects/<int:project_pk>/add-note/", leads_engine.add_project_note_view, name="add_project_note"),
    path("projects/<int:project_pk>/timeline/", leads_engine.project_timeline_view, name="project_timeline"),

    # Worker URLs
    path("workers/", leads_engine.worker_list_view, name="worker_list"),
    path("workers/create/", leads_engine.worker_create_view, name="worker_create"),
    path("workers/<int:pk>/", leads_engine.worker_detail_view, name="worker_detail"),
    path("workers/top-performers/", leads_engine.top_performers_view, name="top_performers"),

    # Revenue URLs
    path("revenue/create/", leads_engine.revenue_create_view, name="revenue_create"),
    path("revenue/create/<int:project_id>/", leads_engine.revenue_create_view, name="revenue_create_for_project"),

    # Requirements URLs
    path("requirements/create/", leads_engine.requirement_create_view, name="requirement_create"),
    
    # Analytics URLs
    path("analytics/dashboard/", leads_engine.analytics_dashboard_view, name="analytics_dashboard"),
    path("analytics/pareto/", leads_engine.pareto_analysis_view, name="pareto_analysis"),
    
    # Risk & Credit URLs
    path("risk/dashboard/", leads_engine.risk_dashboard_view, name="risk_dashboard"),
    
    # Assignment Intelligence URLs
    path("assignments/overview/", leads_engine.assignments_overview_view, name="assignments_overview"),
    path("assignments/intelligence/<int:requirement_id>/", leads_engine.assignment_intelligence_view, name="assignment_intelligence"),
]
