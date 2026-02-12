# Leads Management

# Dashboard View
from django.shortcuts import render
from django.contrib import messages
from ..services.analytics_service import get_dashboard_summary, revenue_per_pincode
from ..services.project_service import get_high_value_opportunities
from ..services.credit_service import generate_credit_alerts
from ..models import Project


def dashboard_view(request):
    """Enhanced dashboard with intelligence"""
    summary = get_dashboard_summary()
    pincode_data = revenue_per_pincode()[:10]  # Top 10
    high_value_opps = get_high_value_opportunities(min_opportunity=100000, limit=5)
    recent_alerts = generate_credit_alerts()[:5]  # Top 5 alerts
    messages.info(request, "Dashboard loaded successfully!")
    context = {
        "summary": summary,
        "pincode_data": pincode_data,
        "high_value_opportunities": high_value_opps,
        "recent_alerts": recent_alerts,
        "total_projects": summary['projects']['total_projects']
    }

    return render(request, "leads_engine/dashboard.html", context)

def project_list_view(request):
    projects = Project.objects.select_related(
        "current_stage", "lead_status"
    ).all()

    return render(request, "leads_engine/project_list.html", {"projects": projects})

# Project Create
from django.shortcuts import redirect
from ..forms import ProjectForm, RequirementForm, RevenueForm, WorkerForm
from ..services.project_service import create_project


def project_create_view(request):
    if request.method == "POST":
        form = ProjectForm(request.POST)
        if form.is_valid():
            project = create_project(form.cleaned_data)
            messages.success(request, f"✓ Project '{project.name}' created successfully with code {project.project_code}!")
            return redirect("project_list")
    else:
        form = ProjectForm()

    return render(request, "leads_engine/project_create.html", {"form": form})

# Project Detail
from ..services.project_service import (
    calculate_remaining_opportunity,
    calculate_capture_ratio,
    get_project_stages_with_priority,
    is_late_entry_project
)


def project_detail_view(request, pk):
    project = Project.objects.get(pk=pk)
    remaining = calculate_remaining_opportunity(project)
    capture_ratio = calculate_capture_ratio(project)
    stages_with_priority = get_project_stages_with_priority(project)
    late_entry = is_late_entry_project(project)

    return render(request, "leads_engine/project_detail.html", {
        "project": project,
        "remaining": remaining,
        "capture_ratio": capture_ratio,
        "stages_with_priority": stages_with_priority,
        "late_entry": late_entry
    })

# Worker Create
def worker_create_view(request):
    if request.method == "POST":
        form = WorkerForm(request.POST)
        if form.is_valid():
            worker = form.save()
            messages.success(request, f"✓ Worker '{worker.name}' created successfully with code {worker.worker_code}!")
            return redirect("worker_list")
    else:
        form = WorkerForm()

    return render(request, "leads_engine/worker_create.html", {"form": form})

#  Revenue Create
from ..services.revenue_service import record_transaction


def revenue_create_view(request):
    if request.method == "POST":
        form = RevenueForm(request.POST)
        if form.is_valid():
            record_transaction(form.cleaned_data)
            return redirect("project_list")
    else:
        form = RevenueForm()

    return render(request, "leads_engine/revenue_create.html", {"form": form})

# Requirement Create
def requirement_create_view(request):
    if request.method == "POST":
        form = RequirementForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("project_list")
    else:
        form = RequirementForm()

    return render(request, "leads_engine/requirement_create.html", {"form": form})


# =====================================================
# WORKER INTELLIGENCE VIEWS
# =====================================================

from ..services.worker_service import (
    get_worker_performance_summary,
    get_top_performers,
    calculate_influence_score,
    calculate_loyalty_score,
    calculate_reliability_score,
    calculate_availability_score
)
from ..models import Worker


def worker_list_view(request):
    """Worker list with performance scores"""
    workers = Worker.objects.select_related('role').all()
    
    # Enhance with scores
    workers_data = []
    for worker in workers:
        workers_data.append({
            'worker': worker,
            'influence_score': calculate_influence_score(worker),
            'availability_score': calculate_availability_score(worker),
        })
    
    return render(request, "leads_engine/worker_list.html", {"workers_data": workers_data})


def worker_detail_view(request, pk):
    """Detailed worker performance view"""
    worker = Worker.objects.get(pk=pk)
    performance = get_worker_performance_summary(worker)
    
    # Get credit risk assessment
    from ..services.credit_service import assess_worker_risk, get_credit_action
    risk_level, risk_flags, risk_summary = assess_worker_risk(worker)
    credit_action = get_credit_action(risk_level, worker)
    
    # Get worker's projects
    worker_projects = worker.worker_projects.select_related('project', 'role').all()
    
    context = {
        'worker': worker,
        'performance': performance,
        'risk_level': risk_level,
        'risk_flags': risk_flags,
        'risk_summary': risk_summary,
        'credit_action': credit_action,
        'worker_projects': worker_projects
    }
    
    return render(request, "leads_engine/worker_detail.html", context)


def top_performers_view(request):
    """View for top performing workers"""
    top_workers = get_top_performers(limit=20)
    
    context = {
        'top_workers': top_workers
    }
    
    return render(request, "leads_engine/top_performers.html", context)


# =====================================================
# ANALYTICS DASHBOARD VIEWS
# =====================================================

from ..services.analytics_service import (
    get_dashboard_summary,
    revenue_per_pincode,
    revenue_per_stage,
    calculate_stage_dropoff,
    get_pincode_heatmap_data,
    perform_pareto_analysis,
    analyze_worker_assignment_impact,
    get_project_statistics,
    get_revenue_summary
)


def analytics_dashboard_view(request):
    """Comprehensive analytics dashboard"""
    summary = get_dashboard_summary()
    pincode_data = revenue_per_pincode()
    stage_data = revenue_per_stage()
    dropoff_data = calculate_stage_dropoff()
    
    context = {
        'summary': summary,
        'pincode_data': pincode_data,
        'stage_data': stage_data,
        'dropoff_data': dropoff_data,
    }
    
    return render(request, "leads_engine/analytics_dashboard.html", context)


def revenue_analytics_view(request):
    """Detailed revenue analytics"""
    revenue_summary = get_revenue_summary(days=30)
    stage_revenue = revenue_per_stage()
    pincode_revenue = revenue_per_pincode()
    
    context = {
        'revenue_summary': revenue_summary,
        'stage_revenue': stage_revenue,
        'pincode_revenue': pincode_revenue,
    }
    
    return render(request, "leads_engine/revenue_analytics.html", context)


def pareto_analysis_view(request):
    """Pareto analysis (80/20 rule) view"""
    pareto_data = perform_pareto_analysis()
    worker_impact = analyze_worker_assignment_impact()
    
    context = {
        'pareto_data': pareto_data,
        'worker_impact': worker_impact,
    }
    
    return render(request, "leads_engine/pareto_analysis.html", context)


# =====================================================
# RISK & CREDIT DASHBOARD
# =====================================================

from ..services.credit_service import (
    generate_credit_alerts,
    get_workers_at_risk,
    get_outstanding_by_worker
)


def risk_dashboard_view(request):
    """Credit risk and alerts dashboard"""
    alerts = generate_credit_alerts()
    workers_at_risk = get_workers_at_risk()
    outstanding_list = get_outstanding_by_worker()
    
    context = {
        'alerts': alerts,
        'workers_at_risk': workers_at_risk,
        'outstanding_list': outstanding_list[:20],  # Top 20
        'total_outstanding': sum(item['outstanding'] for item in outstanding_list)
    }
    
    return render(request, "leads_engine/risk_dashboard.html", context)


# =====================================================
# ASSIGNMENT INTELLIGENCE
# =====================================================

from ..services.assignment_service import (
    find_best_workers,
    get_unfilled_requirements,
    get_active_assignments,
    get_assignment_statistics
)
from ..models import ProjectWorkerRequirement


def assignment_intelligence_view(request, requirement_id):
    """View to show best worker matches for a requirement"""
    requirement = ProjectWorkerRequirement.objects.select_related(
        'project', 'role', 'urgency', 'status'
    ).get(pk=requirement_id)
    best_workers = find_best_workers(requirement, limit=10)
    
    context = {
        'requirement': requirement,
        'best_workers': best_workers
    }
    
    return render(request, "leads_engine/assignment_intelligence.html", context)


def assignments_overview_view(request):
    """Overview of all assignments and requirements"""
    unfilled = get_unfilled_requirements(days_ahead=14)
    active_assignments = get_active_assignments()
    stats = get_assignment_statistics()
    
    context = {
        'unfilled_requirements': unfilled,
        'active_assignments': active_assignments,
        'statistics': stats
    }
    
    return render(request, "leads_engine/assignments_overview.html", context)