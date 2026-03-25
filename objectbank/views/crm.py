# Django imports
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required

from ..models import (
    Worker, JobRole
)
from ..forms import (
    WorkerForm
)
# =============== CRM VIEWS ===============
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