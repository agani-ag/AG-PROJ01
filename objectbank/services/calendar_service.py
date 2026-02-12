"""
Calendar Service - Business logic for project calendar and timeline events
"""
from django.db.models import Q
from datetime import datetime, timedelta
from ..models import Project, ProjectActivity, ProjectRevenueTransaction, ProjectNote


# =====================================================
# CALENDAR EVENT GENERATION
# =====================================================

def get_calendar_events(start_date=None, end_date=None, project_id=None):
    """
    Generate calendar events from project activities, transactions, notes, and milestones.
    
    Business Logic:
    - Stage Changes: Blue - Construction progress milestones
    - Revenue Transactions: Green - Cash flow events
    - Project Notes: Yellow - Important updates/decisions
    - Completion Dates: Red - Deadline tracking
    - Project Creation: Purple - Project initiation
    
    Returns: List of event dictionaries for FullCalendar
    """
    events = []
    
    # Filter projects
    projects = Project.objects.all()
    if project_id:
        projects = projects.filter(pk=project_id)
    
    # Date filtering for activities
    activity_filter = Q()
    if start_date:
        activity_filter &= Q(created_at__gte=start_date)
    if end_date:
        activity_filter &= Q(created_at__lte=end_date)
    
    # 1. PROJECT ACTIVITIES (Stage changes, worker assignments, etc.)
    activities = ProjectActivity.objects.select_related(
        'project', 'performed_by'
    ).filter(activity_filter)
    
    if project_id:
        activities = activities.filter(project_id=project_id)
    
    for activity in activities:
        color = '#0d6efd'  # Blue - default
        icon = 'fa-circle'
        
        # Color coding based on activity type
        if activity.activity_type == 'STAGE_CHANGE':
            color = '#0d6efd'  # Blue
            icon = 'fa-tasks'
        elif activity.activity_type == 'WORKER_ASSIGNED':
            color = '#6610f2'  # Purple
            icon = 'fa-user-plus'
        elif activity.activity_type == 'STATUS_CHANGE':
            color = '#fd7e14'  # Orange
            icon = 'fa-flag'
        
        events.append({
            'id': f'activity-{activity.id}',
            'title': f'{activity.project.project_code}: {activity.activity_type.replace("_", " ")}',
            'start': activity.created_at.isoformat(),
            'backgroundColor': color,
            'borderColor': color,
            'textColor': '#ffffff',
            'extendedProps': {
                'type': 'activity',
                'project_id': activity.project.id,
                'project_code': activity.project.project_code,
                'project_name': activity.project.name,
                'description': activity.description,
                'icon': icon,
                'performed_by': activity.performed_by.username if activity.performed_by else 'System'
            }
        })
    
    # 2. REVENUE TRANSACTIONS (Cash flow events)
    revenue_filter = Q()
    if start_date:
        revenue_filter &= Q(transaction_date__gte=start_date)
    if end_date:
        revenue_filter &= Q(transaction_date__lte=end_date)
    
    transactions = ProjectRevenueTransaction.objects.select_related(
        'project', 'stage', 'worker'
    ).filter(revenue_filter)
    
    if project_id:
        transactions = transactions.filter(project_id=project_id)
    
    for txn in transactions:
        events.append({
            'id': f'revenue-{txn.id}',
            'title': f'{txn.project.project_code}: ₹{int(txn.revenue_amount):,} ({txn.stage.name})',
            'start': txn.transaction_date.isoformat(),
            'backgroundColor': '#198754',  # Green
            'borderColor': '#198754',
            'textColor': '#ffffff',
            'extendedProps': {
                'type': 'revenue',
                'project_id': txn.project.id,
                'project_code': txn.project.project_code,
                'project_name': txn.project.name,
                'amount': float(txn.revenue_amount),
                'stage': txn.stage.name,
                'invoice_number': txn.invoice_number,
                'icon': 'fa-indian-rupee-sign',
                'worker': txn.worker.name if txn.worker else None
            }
        })
    
    # 3. PROJECT NOTES (Important updates)
    notes_filter = Q()
    if start_date:
        notes_filter &= Q(created_at__gte=start_date)
    if end_date:
        notes_filter &= Q(created_at__lte=end_date)
    
    notes = ProjectNote.objects.select_related(
        'project', 'created_by'
    ).filter(notes_filter)
    
    if project_id:
        notes = notes.filter(project_id=project_id)
    
    for note in notes:
        # Create truncated title from note text
        note_preview = note.note[:50] + '...' if len(note.note) > 50 else note.note
        
        events.append({
            'id': f'note-{note.id}',
            'title': f'{note.project.project_code}: Note',
            'start': note.created_at.isoformat(),
            'backgroundColor': '#ffc107',  # Yellow
            'borderColor': '#ffc107',
            'textColor': '#000000',
            'extendedProps': {
                'type': 'note',
                'project_id': note.project.id,
                'project_code': note.project.project_code,
                'project_name': note.project.name,
                'note_content': note.note,
                'is_important': note.is_important,
                'icon': 'fa-note-sticky',
                'created_by': note.created_by.username if note.created_by else 'Unknown'
            }
        })
    
    # 4. PROJECT CREATION DATES
    project_filter = Q()
    if start_date:
        project_filter &= Q(created_at__gte=start_date)
    if end_date:
        project_filter &= Q(created_at__lte=end_date)
    
    if project_id:
        project_filter &= Q(pk=project_id)
    
    created_projects = projects.filter(project_filter)
    
    for project in created_projects:
        events.append({
            'id': f'project-created-{project.id}',
            'title': f'{project.project_code}: Project Created',
            'start': project.created_at.isoformat(),
            'backgroundColor': '#6f42c1',  # Purple
            'borderColor': '#6f42c1',
            'textColor': '#ffffff',
            'extendedProps': {
                'type': 'project_created',
                'project_id': project.id,
                'project_code': project.project_code,
                'project_name': project.name,
                'client_name': project.client_name,
                'estimated_value': float(project.estimated_total_value),
                'current_stage': project.current_stage.name,
                'icon': 'fa-plus-circle'
            }
        })
    
    # 5. EXPECTED COMPLETION DATES (Deadlines)
    deadline_filter = Q(expected_completion_date__isnull=False)
    if start_date:
        deadline_filter &= Q(expected_completion_date__gte=start_date)
    if end_date:
        deadline_filter &= Q(expected_completion_date__lte=end_date)
    
    if project_id:
        deadline_filter &= Q(pk=project_id)
    
    projects_with_deadlines = projects.filter(deadline_filter)
    
    for project in projects_with_deadlines:
        # Determine if overdue
        is_overdue = project.expected_completion_date < datetime.now().date()
        
        events.append({
            'id': f'deadline-{project.id}',
            'title': f'{project.project_code}: Expected Completion',
            'start': project.expected_completion_date.isoformat(),
            'backgroundColor': '#dc3545' if is_overdue else '#0dcaf0',  # Red if overdue, cyan if upcoming
            'borderColor': '#dc3545' if is_overdue else '#0dcaf0',
            'textColor': '#ffffff',
            'extendedProps': {
                'type': 'deadline',
                'project_id': project.id,
                'project_code': project.project_code,
                'project_name': project.name,
                'current_stage': project.current_stage.name,
                'is_overdue': is_overdue,
                'icon': 'fa-calendar-xmark' if is_overdue else 'fa-calendar-check'
            }
        })
    
    return events


def get_project_summary_for_calendar():
    """
    Get summary statistics for calendar header
    """
    from django.db.models import Sum, Count
    from datetime import date
    
    total_projects = Project.objects.filter(is_active=True).count()
    active_projects = Project.objects.filter(
        is_active=True,
        lead_status__is_won=True
    ).count()
    
    # Projects with upcoming deadlines (next 30 days)
    upcoming_deadlines = Project.objects.filter(
        is_active=True,
        expected_completion_date__gte=date.today(),
        expected_completion_date__lte=date.today() + timedelta(days=30)
    ).count()
    
    # Overdue projects
    overdue_projects = Project.objects.filter(
        is_active=True,
        expected_completion_date__lt=date.today(),
        lead_status__is_final=False
    ).count()
    
    # Recent activities (last 7 days)
    recent_activities_count = ProjectActivity.objects.filter(
        created_at__gte=datetime.now() - timedelta(days=7)
    ).count()
    
    return {
        'total_projects': total_projects,
        'active_projects': active_projects,
        'upcoming_deadlines': upcoming_deadlines,
        'overdue_projects': overdue_projects,
        'recent_activities': recent_activities_count
    }
