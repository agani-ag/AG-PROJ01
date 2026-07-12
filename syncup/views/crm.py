# Django imports
from django.contrib import messages
from django.http import JsonResponse
from django.db.models import Q
from django.urls import reverse
from django.core.paginator import Paginator
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import get_object_or_404, render, redirect


# CRM is a staff/superuser area (matches the navbar visibility rule). Applied to
# lead views so the URLs can't be reached directly by a non-staff logged-in user.
def _is_crm_staff(user):
    return user.is_authenticated and (user.is_staff or user.is_superuser)

crm_staff_required = user_passes_test(_is_crm_staff)


def _return_after_save(request, fallback, anchor=None):
    """When a record was added/edited from a lead (the `?catch=<lead_id>` links on
    the lead page), return to that lead — scrolled to the relevant section via
    `#anchor` — otherwise go to the feature's list page."""
    catch = request.POST.get('catch') or request.GET.get('catch')
    if catch:
        try:
            url = reverse('lead_view', args=[int(catch)])
            if anchor:
                url += '#' + anchor
            return redirect(url)
        except (TypeError, ValueError):
            pass
    return redirect(fallback)


def _scope_form_to_lead(form, catch):
    """When adding/editing from a lead (?catch=), restrict the lead-related
    dropdowns to that lead and its related records — so the selects show only the
    relevant options instead of every record in the system. The record being
    edited keeps its current values selectable even if they fall outside the set."""
    try:
        lead_id = int(catch)
    except (TypeError, ValueError):
        return
    inst = getattr(form, 'instance', None)
    inst = inst if (inst and inst.pk) else None

    if 'lead' in form.fields:
        form.fields['lead'].queryset = Leads.objects.filter(id=lead_id)
        form.fields['lead'].initial = lead_id
    if 'opportunity' in form.fields:
        qs = Opportunity.objects.filter(lead_id=lead_id)
        if inst and getattr(inst, 'opportunity_id', None):
            qs = qs | Opportunity.objects.filter(id=inst.opportunity_id)
        form.fields['opportunity'].queryset = qs.distinct().order_by('-created_at')
    if 'worker' in form.fields:
        qs = Worker.objects.filter(leads__id=lead_id)
        if inst and getattr(inst, 'worker_id', None):
            qs = qs | Worker.objects.filter(id=inst.worker_id)
        form.fields['worker'].queryset = qs.distinct()


def _status_color_by_label(label):
    """Best-effort Bootstrap colour for an opportunity/material status label."""
    l = (label or "").lower()
    if any(k in l for k in ("won", "paid", "complete", "delivered", "done")):
        return "success"
    if any(k in l for k in ("lost", "fail", "cancel", "reject")):
        return "danger"
    if any(k in l for k in ("progress", "negotiat", "proposal", "pending", "sent")):
        return "warning"
    if "open" in l or "new" in l:
        return "secondary"
    return "info"

from ..models import (
    Opportunity, Worker, JobRole, Leads,
    MaterialRequest, WorkerCommissions, ActivityLog
)
from ..forms import (
    OpportunityForm, WorkerForm, WorkerCommissionsForm,
    MaterialRequestForm, ActivityLogForm, LeadsForm,
)
import json

# =============== JOB ROLE VIEWS ===============
@login_required
def job_roles(request):
    context = {}
    context["job_roles"] = JobRole.objects.all()
    job_categories = list(JobRole.objects.values_list('category', flat=True).distinct())
    context["job_categories"] = job_categories
    return render(request, 'crm/job_roles.html', context)

@login_required
@crm_staff_required
@csrf_exempt
def job_role_create(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        category = request.POST.get('category')
        if name and category:
            if JobRole.objects.filter(name=name.strip().upper()).exists():
                messages.error(request, 'The Job role is already exists.')
                return JsonResponse({'success': False, 'error': 'The Job role is already exists. Use a different name.'})
            JobRole.objects.create(name=name, category=category)
            messages.success(request, 'Job role created successfully.')
            return JsonResponse({'success': True})
        else:
            messages.error(request, 'Name and category are required to create a job role.')
            return JsonResponse({'success': False, 'error': 'Name and category are required to create a job role.'})
    return JsonResponse({'success': False, 'error': 'Invalid request method.'})

# =============== WORKERS VIEWS ===============
@login_required
def workers(request):
    context = {}
    context["workers"] = Worker.objects.select_related('job_role').all()
    return render(request, 'crm/workers.html', context)

@login_required
def worker_add(request):
    context = {}
    catch = request.GET.get('catch') or request.POST.get('catch')
    if request.method == 'POST':
        form = WorkerForm(request.POST)
        if form.is_valid():
            worker = form.save()
            worker.leads.set(form.cleaned_data['leads'])
            # Created from a lead → auto-link the worker to that lead.
            if catch:
                try:
                    worker.leads.add(int(catch))
                except (TypeError, ValueError):
                    pass
            messages.success(request, 'Worker added successfully.')
            return _return_after_save(request, 'workers', 'workers')
        else:
            messages.error(request, form.errors.as_text())
    else:
        form = WorkerForm()
        if catch:  # pre-select the originating lead in the multi-select
            try:
                form.fields['leads'].initial = [int(catch)]
            except (TypeError, ValueError):
                pass
    context['form'] = form
    return render(request, 'crm/worker_edit.html', context)

@login_required
def worker_edit(request, worker_id):
    context = {}
    worker = get_object_or_404(Worker, id=worker_id)
    if request.method == 'POST':
        form = WorkerForm(request.POST, instance=worker)
        if form.is_valid():
            worker = form.save()
            # Save leads assignment
            worker.leads.set(form.cleaned_data['leads'])
            messages.success(request, 'Worker updated successfully.')
            return _return_after_save(request, 'workers', 'workers')
        else:
            messages.error(request, form.errors.as_text())
    else:
        # Pre-populate the multi-select field with current leads
        form = WorkerForm(instance=worker, initial={'leads': worker.leads.all()})
    context['form'] = form
    context['worker'] = worker
    context['is_edit'] = True
    context['catch'] = request.GET.get('catch') or request.POST.get('catch')
    return render(request, 'crm/worker_edit.html', context)

@login_required
def worker_delete(request, worker_id):
    worker = get_object_or_404(Worker, id=worker_id)
    worker.delete()
    messages.success(request, 'Worker deleted successfully.')
    return _return_after_save(request, 'workers', 'workers')

# =============== LEADS VIEWS ===============
# Bootstrap contextual colour for each lead status (shared by list + view badges).
LEAD_STATUS_COLORS = {
    0: "secondary",   # New
    1: "info",        # Contacted
    2: "primary",     # Site Visit
    3: "warning",     # Proposal
    4: "warning",     # Negotiation
    5: "success",     # Won
    6: "danger",      # Lost
}

@login_required
@crm_staff_required
def leads(request):
    qs = Leads.objects.select_related('referral_worker', 'assigned_to').all()
    status = request.GET.get('status', '')
    q = (request.GET.get('q') or '').strip()
    if status != '':
        try:
            qs = qs.filter(status=int(status))
        except (TypeError, ValueError):
            status = ''
    if q:
        qs = qs.filter(
            Q(name__icontains=q) | Q(phone__icontains=q) |
            Q(location__icontains=q) | Q(email__icontains=q)
        )
    paginator = Paginator(qs, 50)
    page_obj = paginator.get_page(request.GET.get('page'))
    for lead in page_obj:
        lead.status_color = LEAD_STATUS_COLORS.get(lead.status, 'secondary')
    context = {
        "page_obj": page_obj,
        "leads": page_obj.object_list,
        "total": paginator.count,
        "status_choices": Leads.STATUS,
        "status_colors": LEAD_STATUS_COLORS,
        "cur_status": status,
        "q": q,
    }
    return render(request, 'crm/leads.html', context)

@login_required
@crm_staff_required
@require_POST
def lead_status_update(request, lead_id):
    """Quick inline status change (AJAX). Returns the new status label + colour."""
    lead = Leads.objects.filter(id=lead_id).first()
    if not lead:
        return JsonResponse({'success': False, 'error': 'Lead not found.'}, status=404)
    try:
        new_status = int(request.POST.get('status'))
    except (TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Invalid status.'}, status=400)
    valid = dict(Leads.STATUS)
    if new_status not in valid:
        return JsonResponse({'success': False, 'error': 'Unknown status value.'}, status=400)
    lead.status = new_status
    lead.save(update_fields=['status', 'updated_at'])
    return JsonResponse({
        'success': True,
        'status': new_status,
        'label': valid[new_status],
        'color': LEAD_STATUS_COLORS.get(new_status, 'secondary'),
    })

@login_required
@crm_staff_required
def lead_view(request, lead_id):
    context = {}
    lead = Leads.objects.filter(id=lead_id).first()
    if not lead:
        messages.error(request, 'Lead not found.')
        return redirect('leads')
    lead.status_color = LEAD_STATUS_COLORS.get(lead.status, 'secondary')

    opportunities = list(Opportunity.objects.filter(lead=lead).order_by('-created_at'))
    for o in opportunities:
        o.status_color = _status_color_by_label(o.get_status_display())
    material_requests = list(MaterialRequest.objects.filter(lead=lead).order_by('-created_at'))
    for m in material_requests:
        m.status_color = _status_color_by_label(m.get_status_display())

    context['activity_logs'] = ActivityLog.objects.filter(lead=lead).select_related('user').order_by('-created_at')
    context['material_requests'] = material_requests
    context['opportunities'] = opportunities
    context['commissions'] = WorkerCommissions.objects.filter(lead=lead).select_related('worker', 'opportunity').order_by('-created_at')
    context['lead'] = lead
    context['status_choices'] = Leads.STATUS
    context['activity_types'] = ActivityLog.TYPE
    return render(request, 'crm/lead_view.html', context)

@login_required
@crm_staff_required
@require_POST
def lead_add_activity(request, lead_id):
    """Inline quick-add of an activity/note from the lead view."""
    lead = Leads.objects.filter(id=lead_id).first()
    if not lead:
        messages.error(request, 'Lead not found.')
        return redirect('leads')
    lead_url = reverse('lead_view', args=[lead.id]) + '#activity'
    description = (request.POST.get('description') or '').strip()
    if not description:
        messages.error(request, 'Please enter a note before adding.')
        return redirect(lead_url)
    try:
        atype = int(request.POST.get('type', 0))
    except (TypeError, ValueError):
        atype = 0
    ActivityLog.objects.create(
        lead=lead,
        user=getattr(request.user, 'userprofile', None),
        type=atype,
        description=description,
        follow_up_date=request.POST.get('follow_up_date') or None,
    )
    messages.success(request, 'Activity added.')
    return redirect(lead_url)

@login_required
@crm_staff_required
@require_POST
def lead_convert(request, lead_id):
    """Shortcut: advance the lead in the pipeline and open a pre-filled Opportunity."""
    lead = Leads.objects.filter(id=lead_id).first()
    if not lead:
        messages.error(request, 'Lead not found.')
        return redirect('leads')
    if lead.status < 3:  # advance to at least "Proposal"
        lead.status = 3
        lead.save(update_fields=['status', 'updated_at'])
    return redirect(reverse('opportunity_add') + f'?catch={lead.id}')

@login_required
@crm_staff_required
def lead_add(request):
    context = {}
    if request.method == 'POST':
        form = LeadsForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Lead added successfully.')
            return redirect('leads')
        else:
            messages.error(request, 'Please correct the highlighted fields.')
    else:
        form = LeadsForm()
    context['form'] = form
    return render(request, 'crm/lead_edit.html', context)

@login_required
@crm_staff_required
def lead_edit(request, lead_id):
    context = {}
    lead = Leads.objects.filter(id=lead_id).first()
    if not lead:
        messages.error(request, 'Lead not found.')
        return redirect('leads')
    if request.method == 'POST':
        form = LeadsForm(request.POST, instance=lead)
        if form.is_valid():
            form.save()
            messages.success(request, 'Lead updated successfully.')
            return redirect('leads')
        else:
            messages.error(request, 'Please correct the highlighted fields.')
    else:
        form = LeadsForm(instance=lead)
    context['form'] = form
    context['lead'] = lead
    context['is_edit'] = True
    return render(request, 'crm/lead_edit.html', context)

@login_required
@crm_staff_required
@require_POST
def lead_delete(request, lead_id):
    lead = Leads.objects.filter(id=lead_id).first()
    if lead:
        lead.delete()
        messages.success(request, 'Lead deleted successfully.')
    else:
        messages.error(request, 'Lead not found.')
    return redirect('leads')

# =============== OPPORTUNITY VIEWS ===============
@login_required
def opportunity(request):
    context = {}
    context["opportunities"] = Opportunity.objects.select_related('lead').all()
    return render(request, 'crm/opportunity.html', context)

@login_required
def opportunity_add(request):
    context = {}
    catch = request.GET.get('catch') or request.POST.get('catch')
    if request.method == 'POST':
        form = OpportunityForm(request.POST)
        if catch:
            _scope_form_to_lead(form, catch)
        if form.is_valid():
            form.save()
            messages.success(request, 'Opportunity added successfully.')
            return _return_after_save(request, 'opportunity', 'opportunities')
        else:
            messages.error(request, form.errors.as_text())
    else:
        form = OpportunityForm()
        if catch:
            _scope_form_to_lead(form, catch)
    context['form'] = form
    context['catch'] = catch
    return render(request, 'crm/opportunity_edit.html', context)

@login_required
def opportunity_edit(request, opportunity_id):
    context = {}
    opportunity = Opportunity.objects.filter(id=opportunity_id).first()
    if not opportunity:
        messages.error(request, 'Opportunity not found.')
        return redirect('opportunity')
    catch = request.GET.get('catch') or request.POST.get('catch')
    if request.method == 'POST':
        form = OpportunityForm(request.POST, instance=opportunity)
        if catch:
            _scope_form_to_lead(form, catch)
        if form.is_valid():
            form.save()
            messages.success(request, 'Opportunity updated successfully.')
            return _return_after_save(request, 'opportunity', 'opportunities')
        else:
            messages.error(request, form.errors.as_text())
    else:
        form = OpportunityForm(instance=opportunity)
        if catch:
            _scope_form_to_lead(form, catch)
    context['form'] = form
    context['opportunity'] = opportunity
    context['is_edit'] = True
    context['catch'] = catch
    return render(request, 'crm/opportunity_edit.html', context)

@login_required
def opportunity_delete(request, opportunity_id):
    opportunity = Opportunity.objects.filter(id=opportunity_id).first()
    if opportunity:
        opportunity.delete()
        messages.success(request, 'Opportunity deleted successfully.')
    else:
        messages.error(request, 'Opportunity not found.')
    return _return_after_save(request, 'opportunity', 'opportunities')

# =============== MATERIAL REQUEST VIEWS ===============
@login_required
def material_request(request):
    context = {}
    context["material_requests"] = MaterialRequest.objects.select_related('lead').all()
    return render(request, 'crm/material_request.html', context)

@login_required
def material_request_add(request):
    context = {}
    catch = request.GET.get('catch') or request.POST.get('catch')
    if request.method == 'POST':
        form = MaterialRequestForm(request.POST)
        if catch:
            _scope_form_to_lead(form, catch)
        if form.is_valid():
            obj = form.save(commit=False)
            requirement_data = request.POST.get('requirement')
            try:
                obj.requirement = json.loads(requirement_data) if requirement_data else []
            except (ValueError, TypeError):
                obj.requirement = []
            obj.save()
            messages.success(request, 'Material Request added successfully.')
            return _return_after_save(request, 'material_request', 'materials')
        else:
            messages.error(request, form.errors.as_text())
    else:
        form = MaterialRequestForm()
        if catch:
            _scope_form_to_lead(form, catch)
    context['form'] = form
    context['catch'] = catch
    return render(request, 'crm/material_request_edit.html', context)

@login_required
def material_request_edit(request, material_request_id):
    context = {}
    material_request = MaterialRequest.objects.filter(id=material_request_id).first()
    if not material_request:
        messages.error(request, 'Material Request not found.')
        return redirect('material_request')
    catch = request.GET.get('catch') or request.POST.get('catch')
    if request.method == 'POST':
        form = MaterialRequestForm(request.POST, instance=material_request)
        if catch:
            _scope_form_to_lead(form, catch)
        if form.is_valid():
            obj = form.save(commit=False)
            requirement_data = request.POST.get('requirement')
            try:
                obj.requirement = json.loads(requirement_data) if requirement_data else []
            except (ValueError, TypeError):
                obj.requirement = []
            obj.save()
            messages.success(request, 'Material Request updated successfully.')
            return _return_after_save(request, 'material_request', 'materials')
        else:
            messages.error(request, form.errors.as_text())
    else:
        form = MaterialRequestForm(instance=material_request)
        if catch:
            _scope_form_to_lead(form, catch)
    context['form'] = form
    context['material_request'] = material_request
    context['is_edit'] = True
    context['catch'] = catch
    return render(request, 'crm/material_request_edit.html', context)

@login_required
def material_request_delete(request, material_request_id):
    material_request = MaterialRequest.objects.filter(id=material_request_id).first()
    if material_request:
        material_request.delete()
        messages.success(request, 'Material Request deleted successfully.')
    else:
        messages.error(request, 'Material Request not found.')
    return _return_after_save(request, 'material_request', 'materials')

# ============== WORKER COMMISSIONS VIEWS ===============
@login_required
def worker_commissions(request):
    context = {}
    context["commissions"] = WorkerCommissions.objects.select_related('lead', 'worker', 'opportunity').all()
    return render(request, 'crm/worker_commissions.html', context)

@login_required
def worker_commissions_add(request):
    context = {}
    catch = request.GET.get('catch') or request.POST.get('catch')
    if request.method == 'POST':
        form = WorkerCommissionsForm(request.POST)
        if catch:
            _scope_form_to_lead(form, catch)
        if form.is_valid():
            form.save()
            messages.success(request, 'Worker Commission added successfully.')
            return _return_after_save(request, 'worker_commissions', 'commissions')
        else:
            messages.error(request, form.errors.as_text())
    else:
        form = WorkerCommissionsForm()
        if catch:
            _scope_form_to_lead(form, catch)
    context['form'] = form
    context['catch'] = catch
    return render(request, 'crm/worker_commissions_edit.html', context)

@login_required
def worker_commissions_edit(request, commission_id):
    context = {}
    commission = WorkerCommissions.objects.filter(id=commission_id).first()
    if not commission:
        messages.error(request, 'Worker Commission not found.')
        return redirect('worker_commissions')
    catch = request.GET.get('catch') or request.POST.get('catch')
    if request.method == 'POST':
        form = WorkerCommissionsForm(request.POST, instance=commission)
        if catch:
            _scope_form_to_lead(form, catch)
        if form.is_valid():
            form.save()
            messages.success(request, 'Worker Commission updated successfully.')
            return _return_after_save(request, 'worker_commissions', 'commissions')
        else:
            messages.error(request, form.errors.as_text())
    else:
        form = WorkerCommissionsForm(instance=commission)
        if catch:
            _scope_form_to_lead(form, catch)
    context['form'] = form
    context['commission'] = commission
    context['is_edit'] = True
    context['catch'] = catch
    return render(request, 'crm/worker_commissions_edit.html', context)

@login_required
def worker_commissions_delete(request, commission_id):
    commission = WorkerCommissions.objects.filter(id=commission_id).first()
    if commission:
        commission.delete()
        messages.success(request, 'Worker Commission deleted successfully.')
    else:
        messages.error(request, 'Worker Commission not found.')
    return _return_after_save(request, 'worker_commissions', 'commissions')

# ============== ACTIVITY LOG VIEWS ===============
@login_required
def activity_log(request):
    context = {}
    context["activity_logs"] = ActivityLog.objects.select_related('lead', 'user').all()
    return render(request, 'crm/activity_log.html', context)

@login_required
def activity_log_add(request):
    context = {}
    catch = request.GET.get('catch') or request.POST.get('catch')
    if request.method == 'POST':
        form = ActivityLogForm(request.POST)
        if catch:
            _scope_form_to_lead(form, catch)
        if form.is_valid():
            form.save()
            messages.success(request, 'Activity Log added successfully.')
            return _return_after_save(request, 'activity_log', 'activity')
        else:
            messages.error(request, form.errors.as_text())
    else:
        form = ActivityLogForm()
        if catch:
            _scope_form_to_lead(form, catch)
    context['form'] = form
    context['catch'] = catch
    return render(request, 'crm/activity_log_edit.html', context)

@login_required
def activity_log_edit(request, log_id):
    context = {}
    log = ActivityLog.objects.filter(id=log_id).first()
    if not log:
        messages.error(request, 'Activity Log not found.')
        return redirect('activity_log')
    catch = request.GET.get('catch') or request.POST.get('catch')
    if request.method == 'POST':
        form = ActivityLogForm(request.POST, instance=log)
        if catch:
            _scope_form_to_lead(form, catch)
        if form.is_valid():
            form.save()
            messages.success(request, 'Activity Log updated successfully.')
            return _return_after_save(request, 'activity_log', 'activity')
        else:
            messages.error(request, form.errors.as_text())
    else:
        form = ActivityLogForm(instance=log)
        if catch:
            _scope_form_to_lead(form, catch)
    context['form'] = form
    context['log'] = log
    context['is_edit'] = True
    context['catch'] = catch
    return render(request, 'crm/activity_log_edit.html', context)

@login_required
def activity_log_delete(request, log_id):
    log = ActivityLog.objects.filter(id=log_id).first()
    if log:
        log.delete()
        messages.success(request, 'Activity Log deleted successfully.')
    else:
        messages.error(request, 'Activity Log not found.')
    return _return_after_save(request, 'activity_log', 'activity')
