# Django imports
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required

from ..models import (
    Opportunity, Worker, JobRole, Leads,
    MaterialRequest, WorkerCommissions, ActivityLog
)
from ..forms import (
    OpportunityForm, WorkerForm, WorkerCommissionsForm,
    MaterialRequestForm, ActivityLogForm, LeadsForm
)
# =============== JOB ROLE VIEWS ===============
@login_required
def job_roles(request):
    context = {}
    context["job_roles"] = JobRole.objects.all()
    job_categories = list(JobRole.objects.values_list('category', flat=True).distinct())
    context["job_categories"] = job_categories
    return render(request, 'crm/job_roles.html', context)

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
    if request.method == 'POST':
        form = WorkerForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Worker added successfully.')
            return redirect('workers')
        else:
            messages.error(request, form.errors.as_text())
    else:
        form = WorkerForm()
    context['form'] = form
    return render(request, 'crm/worker_edit.html', context)

@login_required
def worker_edit(request, worker_id):
    context = {}
    worker = Worker.objects.filter(id=worker_id).first()
    if not worker:
        messages.error(request, 'Worker not found.')
        return redirect('workers')
    if request.method == 'POST':
        form = WorkerForm(request.POST, instance=worker)
        if form.is_valid():
            form.save()
            messages.success(request, 'Worker updated successfully.')
            return redirect('workers')
        else:
            messages.error(request, form.errors.as_text())
    else:
        form = WorkerForm(instance=worker)
    context['form'] = form
    context['worker'] = worker
    context['is_edit'] = True
    return render(request, 'crm/worker_edit.html', context)

@login_required
def worker_delete(request, worker_id):
    worker = Worker.objects.filter(id=worker_id).first()
    if worker:
        worker.delete()
        messages.success(request, 'Worker deleted successfully.')
    else:
        messages.error(request, 'Worker not found.')
    return redirect('workers')

# =============== LEADS VIEWS ===============
@login_required
def leads(request):
    context = {}
    context["leads"] = Leads.objects.select_related('worker','assigned_to').all()
    return render(request, 'crm/leads.html', context)

@login_required
def lead_add(request):
    context = {}
    if request.method == 'POST':
        form = LeadsForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Lead added successfully.')
            return redirect('leads')
        else:
            messages.error(request, form.errors.as_text())
    else:
        form = LeadsForm()
    context['form'] = form
    return render(request, 'crm/lead_edit.html', context)

@login_required
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
            messages.error(request, form.errors.as_text())
    else:
        form = LeadsForm(instance=lead)
    context['form'] = form
    context['lead'] = lead
    context['is_edit'] = True
    return render(request, 'crm/lead_edit.html', context)

@login_required
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
    if request.method == 'POST':
        form = OpportunityForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Opportunity added successfully.')
            return redirect('opportunity')
        else:
            messages.error(request, form.errors.as_text())
    else:
        form = OpportunityForm()
    context['form'] = form
    return render(request, 'crm/opportunity_edit.html', context)

@login_required
def opportunity_edit(request, opportunity_id):
    context = {}
    opportunity = Opportunity.objects.filter(id=opportunity_id).first()
    if not opportunity:
        messages.error(request, 'Opportunity not found.')
        return redirect('opportunity')
    if request.method == 'POST':
        form = OpportunityForm(request.POST, instance=opportunity)
        if form.is_valid():
            form.save()
            messages.success(request, 'Opportunity updated successfully.')
            return redirect('opportunity')
        else:
            messages.error(request, form.errors.as_text())
    else:
        form = OpportunityForm(instance=opportunity)
    context['form'] = form
    context['opportunity'] = opportunity
    context['is_edit'] = True
    return render(request, 'crm/opportunity_edit.html', context)

@login_required
def opportunity_delete(request, opportunity_id):
    opportunity = Opportunity.objects.filter(id=opportunity_id).first()
    if opportunity:
        opportunity.delete()
        messages.success(request, 'Opportunity deleted successfully.')
    else:
        messages.error(request, 'Opportunity not found.')
    return redirect('opportunity')

# =============== MATERIAL REQUEST VIEWS ===============
@login_required
def material_request(request):
    context = {}
    context["material_requests"] = MaterialRequest.objects.select_related('lead').all()
    return render(request, 'crm/material_request.html', context)

@login_required
def material_request_add(request):
    context = {}
    if request.method == 'POST':
        form = MaterialRequestForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Material Request added successfully.')
            return redirect('material_request')
        else:
            messages.error(request, form.errors.as_text())
    else:
        form = MaterialRequestForm()
    context['form'] = form
    return render(request, 'crm/material_request_edit.html', context)

@login_required
def material_request_edit(request, material_request_id):
    context = {}
    material_request = MaterialRequest.objects.filter(id=material_request_id).first()
    if not material_request:
        messages.error(request, 'Material Request not found.')
        return redirect('material_request')
    if request.method == 'POST':
        form = MaterialRequestForm(request.POST, instance=material_request)
        if form.is_valid():
            form.save()
            messages.success(request, 'Material Request updated successfully.')
            return redirect('material_request')
        else:
            messages.error(request, form.errors.as_text())
    else:
        form = MaterialRequestForm(instance=material_request)
    context['form'] = form
    context['material_request'] = material_request
    context['is_edit'] = True
    return render(request, 'crm/material_request_edit.html', context)

@login_required
def material_request_delete(request, material_request_id):
    material_request = MaterialRequest.objects.filter(id=material_request_id).first()
    if material_request:
        material_request.delete()
        messages.success(request, 'Material Request deleted successfully.')
    else:
        messages.error(request, 'Material Request not found.')
    return redirect('material_request')

# ============== WORKER COMMISSIONS VIEWS ===============
@login_required
def worker_commissions(request):
    context = {}
    context["commissions"] = WorkerCommissions.objects.select_related('lead', 'worker', 'opportunity').all()
    return render(request, 'crm/worker_commissions.html', context)

@login_required
def worker_commissions_add(request):
    context = {}
    if request.method == 'POST':
        form = WorkerCommissionsForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Worker Commission added successfully.')
            return redirect('worker_commissions')
        else:
            messages.error(request, form.errors.as_text())
    else:
        form = WorkerCommissionsForm()
    context['form'] = form
    return render(request, 'crm/worker_commissions_edit.html', context)

@login_required
def worker_commissions_edit(request, commission_id):
    context = {}
    commission = WorkerCommissions.objects.filter(id=commission_id).first()
    if not commission:
        messages.error(request, 'Worker Commission not found.')
        return redirect('worker_commissions')
    if request.method == 'POST':
        form = WorkerCommissionsForm(request.POST, instance=commission)
        if form.is_valid():
            form.save()
            messages.success(request, 'Worker Commission updated successfully.')
            return redirect('worker_commissions')
        else:
            messages.error(request, form.errors.as_text())
    else:
        form = WorkerCommissionsForm(instance=commission)
    context['form'] = form
    context['commission'] = commission
    context['is_edit'] = True
    return render(request, 'crm/worker_commissions_edit.html', context)

@login_required
def worker_commissions_delete(request, commission_id):
    commission = WorkerCommissions.objects.filter(id=commission_id).first()
    if commission:
        commission.delete()
        messages.success(request, 'Worker Commission deleted successfully.')
    else:
        messages.error(request, 'Worker Commission not found.')
    return redirect('worker_commissions')

# ============== ACTIVITY LOG VIEWS ===============
@login_required
def activity_log(request):
    context = {}
    context["activity_logs"] = ActivityLog.objects.select_related('lead', 'user').all()
    return render(request, 'crm/activity_log.html', context)

@login_required
def activity_log_add(request):
    context = {}
    if request.method == 'POST':
        form = ActivityLogForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Activity Log added successfully.')
            return redirect('activity_log')
        else:
            messages.error(request, form.errors.as_text())
    else:
        form = ActivityLogForm()
    context['form'] = form
    return render(request, 'crm/activity_log_edit.html', context)

@login_required
def activity_log_edit(request, log_id):
    context = {}
    log = ActivityLog.objects.filter(id=log_id).first()
    if not log:
        messages.error(request, 'Activity Log not found.')
        return redirect('activity_log')
    if request.method == 'POST':
        form = ActivityLogForm(request.POST, instance=log)
        if form.is_valid():
            form.save()
            messages.success(request, 'Activity Log updated successfully.')
            return redirect('activity_log')
        else:
            messages.error(request, form.errors.as_text())
    else:
        form = ActivityLogForm(instance=log)
    context['form'] = form
    context['log'] = log
    context['is_edit'] = True
    return render(request, 'crm/activity_log_edit.html', context)

@login_required
def activity_log_delete(request, log_id):
    log = ActivityLog.objects.filter(id=log_id).first()
    if log:
        log.delete()
        messages.success(request, 'Activity Log deleted successfully.')
    else:
        messages.error(request, 'Activity Log not found.')
    return redirect('activity_log')
