from django.db import transaction
from django.db.models import Sum, F, Q
from django.utils import timezone
from decimal import Decimal
from ..models import Project, ProjectStage, ConstructionStage, ProjectRevenueTransaction


# =====================================================
# PROJECT CREATION & MANAGEMENT
# =====================================================

@transaction.atomic
def create_project(data):
    """
    Create project and auto-create stage breakdown.
    Intelligently distributes estimated_total_value across stages based on priority weights.
    """
    project = Project.objects.create(**data)

    stages = ConstructionStage.objects.filter(is_active=True).order_by('sequence_order')
    
    # Calculate total priority weight for proportional distribution
    total_priority_weight = sum(stage.default_margin_priority for stage in stages)
    
    # Distribute estimated value across stages proportionally based on priority
    if total_priority_weight > 0 and project.estimated_total_value > 0:
        for stage in stages:
            # Calculate stage's share of total value (convert to Decimal for compatibility)
            stage_percentage = Decimal(str(stage.default_margin_priority / total_priority_weight))
            stage_estimated_value = project.estimated_total_value * stage_percentage
            
            # Higher priority stages get higher expected margin (15-30% range)
            # Formula: Base 15% + (priority/10) * 15% = 15% to 30%
            expected_margin = Decimal('15') + Decimal(str((stage.default_margin_priority / 10))) * Decimal('15')
            
            ProjectStage.objects.create(
                project=project,
                stage=stage,
                estimated_stage_value=round(stage_estimated_value, 2),
                expected_margin_percentage=round(expected_margin, 2)
            )
    else:
        # Fallback: Create stages with zero values if no total estimate
        for stage in stages:
            ProjectStage.objects.create(
                project=project,
                stage=stage,
                estimated_stage_value=0,
                expected_margin_percentage=20  # Default 20% margin
            )

    return project


def recalculate_project_stage_estimates(project):
    """
    Recalculate and redistribute stage estimated values for an existing project.
    Useful for projects created before automatic distribution was implemented.
    Preserves captured_stage_revenue values.
    """
    if project.estimated_total_value <= 0:
        return False
    
    stages = ConstructionStage.objects.filter(is_active=True).order_by('sequence_order')
    total_priority_weight = sum(stage.default_margin_priority for stage in stages)
    
    if total_priority_weight <= 0:
        return False
    
    for stage in stages:
        try:
            ps = ProjectStage.objects.get(project=project, stage=stage)
            
            # Calculate new estimated value (convert to Decimal for compatibility)
            stage_percentage = Decimal(str(stage.default_margin_priority / total_priority_weight))
            new_estimated_value = project.estimated_total_value * stage_percentage
            
            # Calculate expected margin
            expected_margin = Decimal('15') + Decimal(str((stage.default_margin_priority / 10))) * Decimal('15')
            
            # Update only if current value is 0 (don't overwrite manual adjustments)
            if ps.estimated_stage_value == 0:
                ps.estimated_stage_value = round(new_estimated_value, 2)
            
            # Always update margin if it's 0
            if ps.expected_margin_percentage == 0:
                ps.expected_margin_percentage = round(expected_margin, 2)
            
            ps.save(update_fields=['estimated_stage_value', 'expected_margin_percentage'])
            
        except ProjectStage.DoesNotExist:
            # Create missing stage (convert to Decimal for compatibility)
            stage_percentage = Decimal(str(stage.default_margin_priority / total_priority_weight))
            stage_estimated_value = project.estimated_total_value * stage_percentage
            expected_margin = Decimal('15') + Decimal(str((stage.default_margin_priority / 10))) * Decimal('15')
            
            ProjectStage.objects.create(
                project=project,
                stage=stage,
                estimated_stage_value=round(stage_estimated_value, 2),
                expected_margin_percentage=round(expected_margin, 2)
            )
    
    return True


def update_lead_status(project, new_status):
    """Update project lead status"""
    project.lead_status = new_status
    project.save(update_fields=["lead_status"])


def update_current_stage(project, new_stage):
    """Update project's current construction stage"""
    project.current_stage = new_stage
    project.save(update_fields=["current_stage"])


def mark_past_stages_completed(project):
    """
    Auto-complete all stages that come before the project's current stage.
    
    Business Logic: When a project moves to a new construction stage,
    all previous stages should be automatically marked as completed.
    This ensures data integrity and reflects real-world construction flow.
    
    Example: If project moves to "Painting", then "Plastering", "Walls", 
    "Structure", "Foundation", etc. should all be marked as completed.
    """
    from datetime import date
    
    if not project.current_stage:
        return 0
    
    current_sequence = project.current_stage.sequence_order
    
    # Get all past stages (sequence_order < current) that are not yet completed
    past_incomplete_stages = ProjectStage.objects.filter(
        project=project,
        stage__sequence_order__lt=current_sequence,
        is_completed=False
    )
    
    # Mark them as completed
    count = past_incomplete_stages.update(
        is_completed=True,
        completed_date=date.today()
    )
    
    return count


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
        stage_remaining = float(stage.estimated_stage_value) - float(stage.captured_stage_revenue)
        remaining += max(stage_remaining, 0)  # Don't count negative

    return round(remaining, 2)


def calculate_remaining_opportunity_with_decay(project):
    """
    Calculate remaining opportunity with decay factors
    Accounts for stage progression and probability
    """
    current_stage_seq = project.current_stage.sequence_order
    stages = project.stages.select_related('stage').all()
    
    remaining = 0
    for ps in stages:
        stage_remaining = float(ps.estimated_stage_value) - float(ps.captured_stage_revenue)
        
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
    total_estimated = float(project.estimated_total_value)
    
    if total_estimated == 0:
        return 0
    
    total_captured = ProjectRevenueTransaction.objects.filter(
        project=project
    ).aggregate(total=Sum('revenue_amount'))['total'] or 0
    
    return round((float(total_captured) / total_estimated) * 100, 2)


def calculate_stage_capture_ratio(project_stage):
    """Calculate capture ratio for a specific project stage"""
    if project_stage.estimated_stage_value == 0:
        return 0
    
    return round(
        (float(project_stage.captured_stage_revenue) / float(project_stage.estimated_stage_value)) * 100,
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
    Production-Ready Priority Calculation with Stage Context Awareness
    
    Score: 0-100
    Components:
    - Stage Position Weight (40%): Current/Next stages get highest priority
    - Revenue Opportunity (25%): Remaining uncaptured value
    - Margin Potential (20%): Expected profitability
    - Capture Urgency (15%): Low capture = high urgency
    
    Business Logic:
    - Past completed stages: Low priority (10-30)
    - Current stage: CRITICAL priority (80-100)
    - Next 1-2 stages: HIGH priority (60-80)
    - Future stages: Medium priority (30-60)
    - Way future stages: Low priority (10-30)
    """
    try:
        ps = ProjectStage.objects.get(project=project, stage=stage)
    except ProjectStage.DoesNotExist:
        return 0
    
    current_seq = project.current_stage.sequence_order
    stage_seq = stage.sequence_order
    position_delta = stage_seq - current_seq
    
    # Component 1: Stage Position Weight (40%) - Context matters!
    if ps.is_completed:
        # Completed stages - low priority unless uncaptured revenue
        position_score = 10
    elif position_delta == 0:
        # CURRENT STAGE - Maximum priority (active work)
        position_score = 100
    elif position_delta == 1:
        # NEXT STAGE - High priority (prepare/mobilize)
        position_score = 85
    elif position_delta == 2:
        # NEXT+1 STAGE - High-Medium priority (advance planning)
        position_score = 70
    elif position_delta > 2 and position_delta <= 4:
        # FUTURE STAGES (2-4 away) - Medium priority
        position_score = 50
    elif position_delta > 4:
        # WAY FUTURE - Low priority (too early to focus)
        position_score = 25
    elif position_delta < 0:
        # PAST STAGES (not completed) - Medium priority (catch-up needed)
        position_score = 40
    else:
        position_score = 50
    
    # Component 2: Revenue Opportunity (25%) - Remaining value to capture
    remaining_value = float(ps.estimated_stage_value - ps.captured_stage_revenue)
    if ps.estimated_stage_value > 0:
        # Normalize to 0-100: ₹1L = 100 points, scales proportionally
        revenue_opportunity_score = min((remaining_value / 100000) * 100, 100)
    else:
        revenue_opportunity_score = 0
    
    # Component 3: Margin Potential (20%) - Profitability weight
    margin_score = float(ps.expected_margin_percentage) if ps.expected_margin_percentage else 20
    # Normalize: 30% margin = 100 points
    margin_normalized = min((margin_score / 30) * 100, 100)
    
    # Component 4: Capture Urgency (15%) - Low capture = high urgency
    capture_ratio = calculate_stage_capture_ratio(ps)
    if not ps.is_completed:
        # Incomplete stages with low capture need attention
        capture_urgency_score = 100 - capture_ratio
    else:
        # Completed stages - urgency only if not fully captured
        capture_urgency_score = max(0, 100 - capture_ratio) * 0.5
    
    # Calculate weighted priority
    priority_score = (
        position_score * 0.40 +
        revenue_opportunity_score * 0.25 +
        margin_normalized * 0.20 +
        capture_urgency_score * 0.15
    )
    
    return round(priority_score, 2)


def get_stage_urgency_level(priority_score, is_completed):
    """
    Determine urgency level based on priority score and completion status
    Returns: urgency level and color
    """
    if is_completed:
        if priority_score > 30:
            return 'Follow-up', 'info'  # Has uncaptured revenue
        else:
            return 'Completed', 'success'
    else:
        if priority_score >= 80:
            return 'CRITICAL', 'danger'
        elif priority_score >= 60:
            return 'High', 'warning'
        elif priority_score >= 40:
            return 'Medium', 'primary'
        else:
            return 'Low', 'secondary'


def get_stage_action_recommendation(position_delta, capture_ratio, remaining_value, is_completed):
    """
    Provide actionable business recommendations based on stage context
    """
    if is_completed and capture_ratio >= 95:
        return 'Stage complete - well captured'
    elif is_completed and capture_ratio < 95:
        return f'Follow-up: ₹{int(remaining_value):,} pending capture'
    elif position_delta == 0:
        if capture_ratio < 30:
            return '⚠️ URGENT: Active stage needs revenue capture'
        elif capture_ratio < 70:
            return '🎯 Active: Continue capturing revenue'
        else:
            return '✓ On track: Good progress'
    elif position_delta == 1:
        return '📋 Prepare: Start mobilizing resources'
    elif position_delta == 2:
        return '📅 Plan: Advance planning required'
    elif position_delta > 2 and position_delta <= 4:
        return '🔮 Monitor: Keep in sight'
    elif position_delta > 4:
        return '⏸️ Future: Address later'
    elif position_delta < 0:
        return '⚠️ Catch-up: Past stage incomplete'
    else:
        return 'Monitor'


def get_project_stages_with_priority(project):
    """
    Get project stages with comprehensive analytics and actionable intelligence
    
    Returns stages in NATURAL SEQUENCE with:
    - Priority scores (context-aware)
    - Urgency levels (Critical/High/Medium/Low)
    - Action recommendations
    - Position indicators
    
    Business Logic: Maintains construction sequence while highlighting priorities
    """
    stages_data = []
    current_seq = project.current_stage.sequence_order
    
    # Get all stages ordered by natural sequence
    for ps in project.stages.select_related('stage').order_by('stage__sequence_order'):
        priority = calculate_stage_priority_score(project, ps.stage)
        capture_ratio = calculate_stage_capture_ratio(ps)
        remaining = ps.estimated_stage_value - ps.captured_stage_revenue
        position_delta = ps.stage.sequence_order - current_seq
        
        # Determine urgency level
        urgency_level, urgency_color = get_stage_urgency_level(priority, ps.is_completed)
        
        # Get action recommendation
        action = get_stage_action_recommendation(
            position_delta, 
            capture_ratio, 
            float(remaining),
            ps.is_completed
        )
        
        # Determine position label
        if ps.stage == project.current_stage:
            position_label = 'Current'
        elif position_delta == 1:
            position_label = 'Next'
        elif position_delta == 2:
            position_label = 'Next+1'
        elif position_delta > 2:
            position_label = 'Future'
        elif position_delta < 0 and not ps.is_completed:
            position_label = 'Past (Incomplete)'
        elif ps.is_completed:
            position_label = 'Completed'
        else:
            position_label = ''
        
        stages_data.append({
            'project_stage': ps,
            'stage': ps.stage,
            'priority_score': priority,
            'capture_ratio': capture_ratio,
            'remaining': remaining,
            'urgency_level': urgency_level,
            'urgency_color': urgency_color,
            'action_recommendation': action,
            'position_label': position_label,
            'position_delta': position_delta,
            'is_current': ps.stage == project.current_stage,
            'is_next': position_delta == 1,
            'is_past': position_delta < 0,
            'is_future': position_delta > 2,
            'is_completed': ps.is_completed
        })
    
    # Return in NATURAL SEQUENCE (construction order)
    # Visual priority handled by urgency colors and indicators
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
