from django.db import transaction
from django.db.models import Sum, F, Q
from django.utils import timezone
from ..models import Project, ProjectStage, ConstructionStage, ProjectRevenueTransaction


# =====================================================
# PROJECT CREATION & MANAGEMENT
# =====================================================

@transaction.atomic
def create_project(data):
    """
    Create project and auto-create stage breakdown.
    """
    project = Project.objects.create(**data)

    stages = ConstructionStage.objects.filter(is_active=True).order_by('sequence_order')

    for stage in stages:
        ProjectStage.objects.create(
            project=project,
            stage=stage,
            estimated_stage_value=0,
            expected_margin_percentage=0
        )

    return project


def update_lead_status(project, new_status):
    """Update project lead status"""
    project.lead_status = new_status
    project.save(update_fields=["lead_status"])


def update_current_stage(project, new_stage):
    """Update project's current construction stage"""
    project.current_stage = new_stage
    project.save(update_fields=["current_stage"])


# =====================================================
# OPPORTUNITY CALCULATIONS
# =====================================================

def calculate_remaining_opportunity(project):
    """
    Calculate total revenue still available to capture
    Simple version: Sum(estimated - captured) for all stages
    """
    stages = project.stages.all()

    remaining = 0
    for stage in stages:
        stage_remaining = stage.estimated_stage_value - stage.captured_stage_revenue
        remaining += max(stage_remaining, 0)  # Don't count negative

    return remaining


def calculate_remaining_opportunity_with_decay(project):
    """
    Calculate remaining opportunity with decay factors
    Accounts for stage progression and probability
    """
    current_stage_seq = project.current_stage.sequence_order
    stages = project.stages.select_related('stage').all()
    
    remaining = 0
    for ps in stages:
        stage_remaining = ps.estimated_stage_value - ps.captured_stage_revenue
        
        # Apply decay factor based on stage position
        if ps.stage.sequence_order == current_stage_seq:
            # Current stage: assume 50% already captured (even if not recorded)
            decay_factor = 0.50
        elif ps.stage.sequence_order == current_stage_seq + 1:
            # Next stage: high probability
            decay_factor = 0.90
        elif ps.stage.sequence_order > current_stage_seq:
            # Future stages: full potential
            decay_factor = 1.0
        else:
            # Past stages: zero (already passed)
            decay_factor = 0
        
        remaining += max(stage_remaining * decay_factor, 0)
    
    return round(remaining, 2)


def calculate_capture_ratio(project):
    """
    Measure how much of estimated value has been captured
    Returns: Percentage (0-100+)
    """
    total_estimated = project.estimated_total_value
    
    if total_estimated == 0:
        return 0
    
    total_captured = ProjectRevenueTransaction.objects.filter(
        project=project
    ).aggregate(total=Sum('revenue_amount'))['total'] or 0
    
    return round((total_captured / total_estimated) * 100, 2)


def calculate_stage_capture_ratio(project_stage):
    """Calculate capture ratio for a specific project stage"""
    if project_stage.estimated_stage_value == 0:
        return 0
    
    return round(
        (project_stage.captured_stage_revenue / project_stage.estimated_stage_value) * 100,
        2
    )


# =====================================================
# STAGE INTELLIGENCE
# =====================================================

def predict_remaining_stages(project):
    """
    Predict which stages are yet to come
    Returns: List of ConstructionStage objects
    """
    current_stage_seq = project.current_stage.sequence_order
    
    remaining_stages = ConstructionStage.objects.filter(
        sequence_order__gt=current_stage_seq,
        is_active=True
    ).order_by('sequence_order')
    
    return remaining_stages


def calculate_stage_priority_score(project, stage):
    """
    Rank which stages to focus on for maximum business value
    Score: 0-100
    Components: Revenue Potential (40%) + Margin (30%) + Priority (20%) + Opportunity (10%)
    """
    try:
        ps = ProjectStage.objects.get(project=project, stage=stage)
    except ProjectStage.DoesNotExist:
        return 0
    
    # Component 1: Revenue Potential (40%)
    revenue_potential_score = min(ps.estimated_stage_value / 10000, 100)
    
    # Component 2: Margin Percentage (30%)
    margin_score = float(ps.expected_margin_percentage) if ps.expected_margin_percentage else 0
    
    # Component 3: Master Margin Priority (20%)
    margin_priority_score = (stage.default_margin_priority / 10) * 100
    
    # Component 4: Capture Status (10%) - Higher score if uncaptured
    capture_ratio = calculate_stage_capture_ratio(ps)
    capture_opportunity_score = 100 - capture_ratio
    
    priority_score = (
        revenue_potential_score * 0.40 +
        margin_score * 0.30 +
        margin_priority_score * 0.20 +
        capture_opportunity_score * 0.10
    )
    
    return round(priority_score, 2)


def get_project_stages_with_priority(project):
    """Get all project stages with priority scores"""
    stages_data = []
    
    for ps in project.stages.select_related('stage').all():
        priority = calculate_stage_priority_score(project, ps.stage)
        capture_ratio = calculate_stage_capture_ratio(ps)
        
        stages_data.append({
            'project_stage': ps,
            'stage': ps.stage,
            'priority_score': priority,
            'capture_ratio': capture_ratio,
            'remaining': ps.estimated_stage_value - ps.captured_stage_revenue
        })
    
    # Sort by priority score descending
    stages_data.sort(key=lambda x: x['priority_score'], reverse=True)
    
    return stages_data


# =====================================================
# PROJECT ANALYTICS
# =====================================================

def get_project_summary(project):
    """Get comprehensive project summary with intelligence"""
    return {
        'project': project,
        'remaining_opportunity': calculate_remaining_opportunity(project),
        'remaining_opportunity_decay': calculate_remaining_opportunity_with_decay(project),
        'capture_ratio': calculate_capture_ratio(project),
        'total_captured': ProjectRevenueTransaction.objects.filter(
            project=project
        ).aggregate(total=Sum('revenue_amount'))['total'] or 0,
        'transaction_count': ProjectRevenueTransaction.objects.filter(
            project=project
        ).count(),
        'worker_count': project.worker_projects.values('worker').distinct().count(),
        'remaining_stages': predict_remaining_stages(project).count(),
    }


def get_high_value_opportunities(min_opportunity=100000, limit=10):
    """Get projects with high remaining opportunity"""
    projects = Project.objects.filter(
        is_active=True,
        lead_status__is_won=False
    ).select_related('current_stage', 'lead_status')
    
    opportunities = []
    for project in projects:
        remaining = calculate_remaining_opportunity(project)
        if remaining >= min_opportunity:
            opportunities.append({
                'project': project,
                'remaining_opportunity': remaining,
                'capture_ratio': calculate_capture_ratio(project)
            })
    
    # Sort by remaining opportunity descending
    opportunities.sort(key=lambda x: x['remaining_opportunity'], reverse=True)
    
    return opportunities[:limit]


def is_late_entry_project(project):
    """
    Determine if project was entered late (mid-stage)
    Returns: True if past stages have zero captured revenue
    """
    current_stage_seq = project.current_stage.sequence_order
    
    past_stages = ProjectStage.objects.filter(
        project=project,
        stage__sequence_order__lt=current_stage_seq
    )
    
    if not past_stages.exists():
        return False
    
    # Check if all past stages have zero captured revenue
    total_past_revenue = past_stages.aggregate(
        total=Sum('captured_stage_revenue')
    )['total'] or 0
    
    return total_past_revenue == 0
