from django.urls import path
from .views import (
    auth, views, link_registry,
    profile, attendance, crm,
    device_access, test
)

urlpatterns = [
    # Home URL
    path('', views.home, name='home'),

    # Auth URLs
    path('login', auth.login_view, name='login'),
    path('signup', auth.signup_view, name='signup'),
    path('logout', auth.logout_view, name='logout'),
    # API URLs
    path('api/auth/login', auth.auth_login_api, name='api_login'),
    path('download/sqlite', auth.download_sqlite, name='download_sqlite'),
    path('api/auth/reset-password', auth.reset_password_api, name='api_reset_password'),
    path('api/telegram/send', views.send_telegram_message_api, name='send_telegram_message_api'),

    # Profile URL
    path('profiles', profile.profiles, name='profiles'),
    path('profile/edit/', profile.profile_edit, name='profile_edit'),
    path('profile/delete/<int:user_id>/', profile.profile_delete, name='profile_delete'),
    path('profile/admin/edit/<int:user_id>/', profile.admin_profile_edit, name='profile_admin_edit'),

    # Public User URLs
    path('public-users', profile.public_users, name='public_users'),
    path('public-user/add', profile.public_user_add, name='public_user_add'),
    path('public-user/edit/<int:public_user_id>', profile.public_user_edit, name='public_user_edit'),
    path('public-user/delete/<int:public_user_id>', profile.public_user_delete, name='public_user_delete'),

    # Link Registry URL
    path('link-registry', link_registry.link_registry, name='link_registry'),
    path('link-registry/add', link_registry.link_registry_add, name='link_registry_add'),
    path('link-registry/edit/<int:link_registry_id>', link_registry.link_registry_edit, name='link_registry_edit'),
    path('link-registry/delete/<int:link_registry_id>', link_registry.link_registry_delete, name='link_registry_delete'),

    # Instance Info URL
    path('instances', link_registry.instance_info, name='instance_info'),
    path('instance/add', link_registry.instance_info_add, name='instance_info_add'),
    path('instance/edit/<int:instance_info_id>', link_registry.instance_info_edit, name='instance_info_edit'),
    path('instance/delete/<int:instance_info_id>', link_registry.instance_info_delete, name='instance_info_delete'),

    # Attendance URLs
    path('attendances', attendance.attendances, name='attendances'),
    path('attendance/ajax/mark/', attendance.ajax_mark_attendance, name='ajax_mark_attendance'),
    path('attendance/<int:user_id>/', attendance.attendance_calendar, name='attendance_calendar'),
    path('attendance/ajax/credit-bonus/', attendance.ajax_update_credit_bonus, name='ajax_update_credit_bonus'),

    # ----- CRM URLs -----
    # Job Roles URLs
    path('crm/job-roles', crm.job_roles, name='job_roles'),
    path('crm/job-role/create', crm.job_role_create, name='job_role_create'),
    # Workers URLs
    path('crm/workers', crm.workers, name='workers'),
    path('crm/worker/add', crm.worker_add, name='worker_add'),
    path('crm/worker/edit/<int:worker_id>', crm.worker_edit, name='worker_edit'),
    path('crm/worker/delete/<int:worker_id>', crm.worker_delete, name='worker_delete'),
    # Leads URLs
    path('crm/leads', crm.leads, name='leads'),
    path('crm/lead/add', crm.lead_add, name='lead_add'),
    path('crm/lead/view/<int:lead_id>', crm.lead_view, name='lead_view'),
    path('crm/lead/edit/<int:lead_id>', crm.lead_edit, name='lead_edit'),
    path('crm/lead/delete/<int:lead_id>', crm.lead_delete, name='lead_delete'),
    # Opportunity URLs
    path('crm/opportunity', crm.opportunity, name='opportunity'),
    path('crm/opportunity/add', crm.opportunity_add, name='opportunity_add'),
    path('crm/opportunity/edit/<int:opportunity_id>', crm.opportunity_edit, name='opportunity_edit'),
    path('crm/opportunity/delete/<int:opportunity_id>', crm.opportunity_delete, name='opportunity_delete'),
    # Material Request URLs
    path('crm/material-requests', crm.material_request, name='material_request'),
    path('crm/material-request/add', crm.material_request_add, name='material_request_add'),
    path('crm/material-request/edit/<int:material_request_id>', crm.material_request_edit, name='material_request_edit'),
    path('crm/material-request/delete/<int:material_request_id>', crm.material_request_delete, name='material_request_delete'),
    # Worker Commissions URLs
    path('crm/worker-commissions', crm.worker_commissions, name='worker_commissions'),
    path('crm/worker-commission/add', crm.worker_commissions_add, name='worker_commissions_add'),
    path('crm/worker-commission/edit/<int:commission_id>', crm.worker_commissions_edit, name='worker_commissions_edit'),
    path('crm/worker-commission/delete/<int:commission_id>', crm.worker_commissions_delete, name='worker_commissions_delete'),
    # Activity Log URLs
    path('crm/activity-log', crm.activity_log, name='activity_log'),
    path('crm/activity-log/add', crm.activity_log_add, name='activity_log_add'),
    path('crm/activity-log/edit/<int:log_id>', crm.activity_log_edit, name='activity_log_edit'),
    path('crm/activity-log/delete/<int:log_id>', crm.activity_log_delete, name='activity_log_delete'),

    # Device Access URLs
    path('device/api/metadata', device_access.metadata, name='metadata'),
    path('device/api/login', device_access.device_login, name='device_login'),
    path('device/api/audit-errors', device_access.audit_errors, name='audit_errors'),
    path('device/audit-errors', device_access.audit_errors_list, name='audit_errors_list'),
    path('device/audit-errors/clear', device_access.audit_errors_clear, name='audit_errors_clear'),
    path('device/api/health', device_access.health_check, name='device_health_check'),
    path('device/api/register', device_access.register_device, name='device_register'),
    path('device/api/unregister', device_access.unregister_device, name='device_unregister'),
    # Device Management URLs
    path('device/list', device_access.list_devices, name='device_list'),
    path('device/view/<int:id>', device_access.device_view, name='device_view'),
    path('device/view/<int:id>/api', device_access.device_view_data_api, name='device_view_data_api'),
    path('device/dashboard', device_access.device_dashboard, name='device_dashboard'),
    path('device/delete/<int:id>', device_access.device_delete, name='device_delete'),
    path('device/api/network', device_access.device_network_api, name='device_network_api'),
    path('device/api/table', device_access.device_table_api, name='device_table_api'),
    # Device Notification URL
    path('device/api/notifications/send', device_access.send_notification, name='send_notification'),
    path('device/api/notifications/upload-image', device_access.upload_notification_image, name='upload_notification_image'),
    
    # Test URLs
    path('device/test1', test.test1, name='test1'),
    path('device/test2', test.test2, name='test2'),
]