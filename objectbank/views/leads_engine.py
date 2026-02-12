# Leads Management

# Dashboard View
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db import models
from ..services.analytics_service import get_dashboard_summary, revenue_per_pincode
from ..services.project_service import get_high_value_opportunities
from ..services.credit_service import generate_credit_alerts
from ..models import Project, ProjectActivity, Worker, WorkerProject


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
from ..forms import ProjectForm, RequirementForm, RevenueForm, WorkerForm, ProjectWorkerInlineForm
from ..services.project_service import create_project
from django.forms import formset_factory


def project_create_view(request):
    # Create formset for inline workers (max 5 workers during creation)
    WorkerFormSet = formset_factory(ProjectWorkerInlineForm, extra=3, max_num=10)
    
    if request.method == "POST":
        form = ProjectForm(request.POST)
        worker_formset = WorkerFormSet(request.POST, prefix='workers')
        
        if form.is_valid() and worker_formset.is_valid():
            # Create the project
            project = create_project(form.cleaned_data)
            
            # Log project creation
            ProjectActivity.objects.create(
                project=project,
                activity_type='CREATED',
                description=f"Project '{project.name}' created with code {project.project_code}",
                performed_by=request.user
            )
            
            # Process workers if any were added
            workers_added = 0
            for worker_form in worker_formset:
                if worker_form.cleaned_data.get('worker'):
                    worker = worker_form.cleaned_data['worker']
                    role = worker_form.cleaned_data['role']
                    referred_by = worker_form.cleaned_data.get('referred_by_worker')
                    
                    # Check if worker already assigned to avoid duplicates
                    if not WorkerProject.objects.filter(project=project, worker=worker).exists():
                        WorkerProject.objects.create(
                            project=project,
                            worker=worker,
                            role=role,
                            referred_by_worker=referred_by
                        )
                        
                        # Log worker assignment
                        ProjectActivity.objects.create(
                            project=project,
                            activity_type='WORKER_ASSIGNED',
                            related_worker=worker,
                            performed_by=request.user,
                            description=f"{worker.name} assigned as {role.name}"
                        )
                        
                        workers_added += 1
            
            # Success message
            if workers_added > 0:
                messages.success(
                    request, 
                    f"✓ Project '{project.name}' created with {workers_added} worker(s) assigned!"
                )
            else:
                messages.success(
                    request, 
                    f"✓ Project '{project.name}' created successfully! You can assign workers from the project detail page."
                )
            
            return redirect("project_detail", pk=project.pk)
    else:
        form = ProjectForm()
        worker_formset = WorkerFormSet(prefix='workers')

    return render(request, "leads_engine/project_create.html", {
        "form": form,
        "worker_formset": worker_formset,
    })

# Project Detail
from ..services.project_service import (
    calculate_remaining_opportunity,
    calculate_capture_ratio,
    get_project_stages_with_priority,
    is_late_entry_project,
    recalculate_project_stage_estimates,
    mark_past_stages_completed
)


def project_detail_view(request, pk):
    project = Project.objects.get(pk=pk)
    
    # Auto-fix: Check if stage estimates need recalculation (all zeros)
    # This fixes projects created before intelligent distribution was implemented
    if project.estimated_total_value > 0:
        total_stage_estimates = project.stages.aggregate(
            total=models.Sum('estimated_stage_value')
        )['total'] or 0
        
        # If total stage estimates are 0 but project has estimated value, recalculate
        if total_stage_estimates == 0:
            recalculate_project_stage_estimates(project)
    
    # Auto-fix: Mark past stages as completed if not already done
    # This fixes projects that were moved forward before auto-completion was implemented
    mark_past_stages_completed(project)
    
    remaining = calculate_remaining_opportunity(project)
    capture_ratio = calculate_capture_ratio(project)
    stages_with_priority = get_project_stages_with_priority(project)
    late_entry = is_late_entry_project(project)
    
    # Get assigned workers
    assigned_workers = project.worker_projects.select_related('worker', 'role', 'referred_by_worker').all()
    
    # Get recent activities (last 10)
    recent_activities = project.activities.select_related('related_worker', 'performed_by')[:10]
    
    # Get recent notes (last 5)
    recent_notes = project.notes.select_related('created_by')[:5]

    return render(request, "leads_engine/project_detail.html", {
        "project": project,
        "remaining": remaining,
        "capture_ratio": capture_ratio,
        "stages_with_priority": stages_with_priority,
        "late_entry": late_entry,
        "assigned_workers": assigned_workers,
        "recent_activities": recent_activities,
        "recent_notes": recent_notes,
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
from datetime import date


def revenue_create_view(request, project_id=None):
    project = None
    assigned_workers = []
    existing_transactions = []
    next_invoice_number = None
    total_revenue_so_far = 0
    
    # If project_id provided, get project context
    if project_id:
        project = get_object_or_404(Project, pk=project_id)
        assigned_workers = project.worker_projects.select_related('worker', 'role').all()
        existing_transactions = project.revenue_transactions.select_related('worker', 'stage').order_by('-transaction_date')[:10]
        
        # Calculate next invoice number
        last_invoice_count = project.revenue_transactions.count()
        next_invoice_number = f"{project.project_code}-INV-{str(last_invoice_count + 1).zfill(3)}"
        
        # Calculate total revenue so far
        total_revenue_so_far = project.revenue_transactions.aggregate(
            total=models.Sum('revenue_amount')
        )['total'] or 0
    
    if request.method == "POST":
        form = RevenueForm(request.POST)
        if form.is_valid():
            record_transaction(form.cleaned_data)
            
            # Create activity log
            recorded_project = form.cleaned_data.get('project')
            revenue_amt = form.cleaned_data.get('revenue_amount')
            ProjectActivity.objects.create(
                project=recorded_project,
                activity_type='REVENUE_RECORDED',
                related_worker=form.cleaned_data.get('worker'),
                performed_by=request.user,
                description=f"Revenue recorded: ₹{revenue_amt} for {form.cleaned_data.get('stage').name} stage"
            )
            
            messages.success(request, f"Revenue of ₹{revenue_amt} recorded successfully!")
            return redirect("project_detail", pk=recorded_project.id)
    else:
        # Initialize form with smart defaults if project provided
        initial_data = {}
        if project:
            initial_data['project'] = project
            initial_data['stage'] = project.current_stage
            initial_data['transaction_date'] = date.today()
            if next_invoice_number:
                initial_data['invoice_number'] = next_invoice_number
        
        form = RevenueForm(initial=initial_data)
        
        # If project context exists, filter worker dropdown to only assigned workers
        if project and assigned_workers:
            worker_ids = [wp.worker.id for wp in assigned_workers]
            form.fields['worker'].queryset = Worker.objects.filter(id__in=worker_ids)

    # Calculate remaining opportunity for context
    remaining = None
    capture_ratio = None
    if project:
        remaining = calculate_remaining_opportunity(project)
        capture_ratio = calculate_capture_ratio(project)

    return render(request, "leads_engine/revenue_create.html", {
        "form": form,
        "project": project,
        "assigned_workers": assigned_workers,
        "existing_transactions": existing_transactions,
        "next_invoice_number": next_invoice_number,
        "total_revenue_so_far": total_revenue_so_far,
        "remaining": remaining,
        "capture_ratio": capture_ratio,
    })

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


# =====================================================
# PROJECT LIFECYCLE MANAGEMENT
# =====================================================

from ..forms import (
    AssignWorkerToProjectForm,
    UpdateLeadStatusForm,
    UpdateConstructionStageForm,
    ProjectNoteForm
)
from ..models import ProjectActivity, ProjectNote, WorkerProject


def assign_worker_to_project_view(request, project_pk):
    """Assign a worker to a project"""
    project = Project.objects.get(pk=project_pk)
    
    if request.method == "POST":
        form = AssignWorkerToProjectForm(request.POST)
        if form.is_valid():
            worker_project = form.save(commit=False)
            worker_project.project = project
            worker_project.save()
            
            # Log activity
            ProjectActivity.objects.create(
                project=project,
                activity_type='WORKER_ASSIGNED',
                description=f"Worker {worker_project.worker.name} assigned as {worker_project.role.name}",
                related_worker=worker_project.worker,
                performed_by=request.user
            )
            
            messages.success(
                request,
                f"✓ Worker '{worker_project.worker.name}' assigned to project successfully!"
            )
            return redirect('project_detail', pk=project.pk)
    else:
        form = AssignWorkerToProjectForm()
    
    # Get already assigned workers
    assigned_workers = project.worker_projects.select_related('worker', 'role').all()
    
    context = {
        'project': project,
        'form': form,
        'assigned_workers': assigned_workers
    }
    
    return render(request, "leads_engine/assign_worker.html", context)


def remove_worker_from_project_view(request, project_pk, worker_project_pk):
    """Remove a worker from a project"""
    project = Project.objects.get(pk=project_pk)
    worker_project = WorkerProject.objects.get(pk=worker_project_pk)
    
    worker_name = worker_project.worker.name
    
    # Log activity before deletion
    ProjectActivity.objects.create(
        project=project,
        activity_type='WORKER_REMOVED',
        description=f"Worker {worker_name} removed from project",
        related_worker=worker_project.worker,
        performed_by=request.user
    )
    
    worker_project.delete()
    
    messages.info(request, f"Worker '{worker_name}' removed from project.")
    return redirect('assign_worker_to_project', project_pk=project.pk)


def update_lead_status_view(request, project_pk):
    """Update project lead status"""
    project = Project.objects.get(pk=project_pk)
    
    if request.method == "POST":
        form = UpdateLeadStatusForm(request.POST)
        if form.is_valid():
            old_status = project.lead_status
            new_status = form.cleaned_data['lead_status']
            notes = form.cleaned_data.get('notes', '')
            
            project.lead_status = new_status
            project.save(update_fields=['lead_status'])
            
            # Log activity
            description = f"Lead status changed from '{old_status.name}' to '{new_status.name}'"
            if notes:
                description += f"\nNotes: {notes}"
            
            # Determine activity type
            if new_status.is_won:
                activity_type = 'PROJECT_WON'
            elif new_status.is_lost:
                activity_type = 'PROJECT_LOST'
            else:
                activity_type = 'STATUS_CHANGE'
            
            ProjectActivity.objects.create(
                project=project,
                activity_type=activity_type,
                description=description,
                old_value=old_status.name,
                new_value=new_status.name,
                performed_by=request.user
            )
            
            messages.success(request, f"✓ Lead status updated to '{new_status.name}'")
            return redirect('project_detail', pk=project.pk)
    else:
        form = UpdateLeadStatusForm(initial={'lead_status': project.lead_status})
    
    context = {
        'project': project,
        'form': form
    }
    
    return render(request, "leads_engine/update_lead_status.html", context)


def update_construction_stage_view(request, project_pk):
    """Update project construction stage"""
    project = Project.objects.get(pk=project_pk)
    
    if request.method == "POST":
        form = UpdateConstructionStageForm(request.POST)
        if form.is_valid():
            old_stage = project.current_stage
            new_stage = form.cleaned_data['construction_stage']
            notes = form.cleaned_data.get('notes', '')
            
            project.current_stage = new_stage
            project.save(update_fields=['current_stage'])
            
            # Auto-complete all past stages
            completed_count = mark_past_stages_completed(project)
            
            # Log activity
            description = f"Construction stage changed from '{old_stage.name}' to '{new_stage.name}'"
            if notes:
                description += f"\nNotes: {notes}"
            if completed_count > 0:
                description += f"\n✓ {completed_count} past stage(s) auto-marked as completed"
            
            ProjectActivity.objects.create(
                project=project,
                activity_type='STAGE_CHANGE',
                description=description,
                old_value=old_stage.name,
                new_value=new_stage.name,
                performed_by=request.user
            )
            
            success_msg = f"✓ Construction stage updated to '{new_stage.name}'"
            if completed_count > 0:
                success_msg += f" ({completed_count} past stage(s) completed)"
            messages.success(request, success_msg)
            return redirect('project_detail', pk=project.pk)
    else:
        form = UpdateConstructionStageForm(initial={'construction_stage': project.current_stage})
    
    context = {
        'project': project,
        'form': form
    }
    
    return render(request, "leads_engine/update_construction_stage.html", context)


def add_project_note_view(request, project_pk):
    """Add note/update to project"""
    project = Project.objects.get(pk=project_pk)
    
    if request.method == "POST":
        form = ProjectNoteForm(request.POST)
        if form.is_valid():
            note = form.save(commit=False)
            note.project = project
            note.created_by = request.user
            note.save()
            
            # Log activity
            ProjectActivity.objects.create(
                project=project,
                activity_type='NOTE_ADDED',
                description=f"Note added: {note.note[:100]}{'...' if len(note.note) > 100 else ''}",
                performed_by=request.user
            )
            
            messages.success(request, "✓ Note added successfully!")
            return redirect('project_detail', pk=project.pk)
    else:
        form = ProjectNoteForm()
    
    context = {
        'project': project,
        'form': form,
        'notes': project.notes.all()[:10]  # Show last 10 notes
    }
    
    return render(request, "leads_engine/add_project_note.html", context)


def project_timeline_view(request, project_pk):
    """View complete project timeline/history"""
    project = Project.objects.get(pk=project_pk)
    
    activities = project.activities.select_related(
        'related_worker', 'performed_by'
    ).all()[:50]  # Last 50 activities
    
    notes = project.notes.select_related('created_by').all()
    
    context = {
        'project': project,
        'activities': activities,
        'notes': notes
    }
    
    return render(request, "leads_engine/project_timeline.html", context)