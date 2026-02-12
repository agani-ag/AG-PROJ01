from django.db import transaction
from django.utils import timezone
from ..models import Worker, WorkerAssignment, RequirementStatus, ProjectWorkerRequirement
from .worker_service import (
    calculate_worker_revenue,
    calculate_availability_score,
    get_availability_status
)


# =====================================================
# WORKER MATCHING & SCORING
# =====================================================

def calculate_match_score(worker, requirement):
    """
    Calculate how well a worker matches a requirement
    Score: 0-100
    Components: Role (40%) + Location (30%) + Availability (20%) + Performance (10%)
    """
    scores = {}
    
    # Component 1: Role Match (40%)
    if worker.role == requirement.role:
        scores['role'] = 100
    else:
        scores['role'] = 0
    
    # Component 2: Location Match (30%)
    if worker.primary_pincode == requirement.project.pincode:
        scores['location'] = 100
    else:
        # Could enhance with geodistance in future
        scores['location'] = 0
    
    # Component 3: Availability (20%)
    availability = calculate_availability_score(worker)
    scores['availability'] = availability
    
    # Component 4: Past Performance (10%)
    worker_revenue = calculate_worker_revenue(worker)
    if worker_revenue > 500000:
        scores['performance'] = 100
    elif worker_revenue > 200000:
        scores['performance'] = 70
    elif worker_revenue > 50000:
        scores['performance'] = 40
    else:
        scores['performance'] = 20
    
    # Calculate weighted match score
    match_score = (
        scores['role'] * 0.40 +
        scores['location'] * 0.30 +
        scores['availability'] * 0.20 +
        scores['performance'] * 0.10
    )
    
    return round(match_score, 2), scores


def find_best_workers(requirement, limit=5):
    """
    Find best matching workers for a requirement
    Returns: List of workers with match scores
    """
    # Get candidate workers
    candidates = Worker.objects.filter(
        role=requirement.role,
        active_status=True
    ).select_related('role')
    
    scored_workers = []
    for worker in candidates:
        match_score, breakdown = calculate_match_score(worker, requirement)
        
        # Only include workers with reasonable match scores
        if match_score >= 40:  # Minimum threshold
            scored_workers.append({
                'worker': worker,
                'score': match_score,
                'breakdown': breakdown,
                'availability_status': get_availability_status(breakdown['availability']),
                'total_revenue': calculate_worker_revenue(worker)
            })
    
    # Sort by score DESC
    scored_workers.sort(key=lambda x: x['score'], reverse=True)
    
    return scored_workers[:limit]


def match_workers(requirement):
    """
    Basic worker matching (legacy function - kept for compatibility)
    Returns: QuerySet of matching workers
    """
    return Worker.objects.filter(
        role=requirement.role,
        primary_pincode=requirement.project.pincode,
        active_status=True
    )


# =====================================================
# ASSIGNMENT MANAGEMENT
# =====================================================

@transaction.atomic
def assign_worker(requirement, worker):
    """
    Assign a worker to a requirement
    Creates WorkerAssignment and updates requirement status
    """
    # Create assignment
    assignment = WorkerAssignment.objects.create(
        requirement=requirement,
        worker=worker,
        assigned_date=timezone.now().date()
    )

    # Update requirement status to ASSIGNED
    try:
        assigned_status = RequirementStatus.objects.get(code='ASSIGNED')
        requirement.status = assigned_status
    except RequirementStatus.DoesNotExist:
        # Fallback to status_id if code doesn't exist
        requirement.status_id = 2
    
    requirement.save(update_fields=["status"])

    return assignment


@transaction.atomic
def complete_assignment(assignment, revenue_impact=0):
    """
    Mark an assignment as completed
    """
    assignment.completion_date = timezone.now().date()
    assignment.revenue_impact = revenue_impact
    assignment.save(update_fields=['completion_date', 'revenue_impact'])
    
    # Update requirement status to COMPLETED
    try:
        completed_status = RequirementStatus.objects.get(code='COMPLETED')
        assignment.requirement.status = completed_status
        assignment.requirement.save(update_fields=['status'])
    except RequirementStatus.DoesNotExist:
        pass
    
    return assignment


# =====================================================
# ASSIGNMENT ANALYTICS
# =====================================================

def get_unfilled_requirements(days_ahead=7):
    """
    Get requirements that need workers soon
    """
    from datetime import timedelta
    
    target_date = timezone.now().date() + timedelta(days=days_ahead)
    
    unfilled = ProjectWorkerRequirement.objects.filter(
        required_from_date__lte=target_date,
        assignment__isnull=True  # No assignment yet
    ).select_related('project', 'role', 'urgency', 'status')
    
    return unfilled


def get_active_assignments():
    """Get all active (not completed) assignments"""
    return WorkerAssignment.objects.filter(
        completion_date__isnull=True
    ).select_related('worker', 'requirement', 'requirement__project')


def get_assignment_statistics():
    """Get overall assignment statistics"""
    from django.db.models import Count, Avg
    from datetime import timedelta
    
    total_requirements = ProjectWorkerRequirement.objects.count()
    assigned_count = WorkerAssignment.objects.count()
    active_count = WorkerAssignment.objects.filter(completion_date__isnull=True).count()
    completed_count = WorkerAssignment.objects.filter(completion_date__isnull=False).count()
    
    # Calculate average time to assign (for completed assignments)
    completed_assignments = WorkerAssignment.objects.filter(
        completion_date__isnull=False
    ).select_related('requirement')
    
    if completed_assignments.exists():
        total_days = 0
        count = 0
        for assignment in completed_assignments:
            days_diff = (assignment.assigned_date - assignment.requirement.created_at.date()).days
            if days_diff >= 0:
                total_days += days_diff
                count += 1
        avg_days_to_assign = round(total_days / count, 1) if count > 0 else 0
    else:
        avg_days_to_assign = 0
    
    return {
        'total_requirements': total_requirements,
        'assigned_count': assigned_count,
        'active_assignments': active_count,
        'completed_assignments': completed_count,
        'unfilled_count': total_requirements - assigned_count,
        'avg_days_to_assign': avg_days_to_assign,
        'fill_rate': round((assigned_count / total_requirements * 100), 1) if total_requirements > 0 else 0
    }