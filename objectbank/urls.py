from django.urls import path
from .views import (
    auth, views, link_registry,
    profile, attendance, crm
    
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

    # Attendance URLs
    path('attendance/ajax/mark/', attendance.ajax_mark_attendance, name='ajax_mark_attendance'),
    path('attendance/<int:user_id>/', attendance.attendance_calendar, name='attendance_calendar'),
    path('attendance/ajax/credit-bonus/', attendance.ajax_update_credit_bonus, name='ajax_update_credit_bonus'),

    # CRM URLs
    path('crm/workers', crm.workers, name='workers'),
    path('crm/job-roles', crm.job_roles, name='job_roles'),
    path('crm/worker/add', crm.worker_add, name='worker_add'),
    path('crm/job-role/create', crm.job_role_create, name='job_role_create'),
    path('crm/worker/edit/<int:worker_id>', crm.worker_edit, name='worker_edit'),
    path('crm/worker/delete/<int:worker_id>', crm.worker_delete, name='worker_delete'),
]