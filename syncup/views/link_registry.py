# Django imports
from django.db.models import F
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required

from ..models import (
    LinkRegistry, InstanceInfo,
    UserProfile
)
from ..forms import (
    LinkRegistryForm, InstanceInfoForm
)

# =============== LINK REGISTRY VIEWS ===============
@login_required
def link_registry(request):
    context = {}
    user_filter = request.GET.get('filter')
    queryset = LinkRegistry.objects.all()
    if user_filter:
        queryset = queryset.filter(user__user_id=user_filter)
    context["link_registries"] = queryset
    context["users"] = UserProfile.objects.values_list('name','user_id')
    context["selected_user"] = user_filter
    return render(request, 'link_registry/link_registry.html', context)

@login_required
def link_registry_add(request):
    context = {}
    if request.method == 'POST':
        form = LinkRegistryForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Link Registry added successfully.')
            return redirect('link_registry')
        else:
            messages.error(request, form.errors.as_text())
    else:
        form = LinkRegistryForm()
    context['form'] = form
    return render(request, 'link_registry/link_registry_edit.html', context)

@login_required
def link_registry_edit(request, link_registry_id):
    context = {}
    link_registry = LinkRegistry.objects.filter(id=link_registry_id).first()
    if not link_registry:
        messages.error(request, 'Link Registry not found.')
        return redirect('link_registry')
    if request.method == 'POST':
        form = LinkRegistryForm(request.POST, instance=link_registry)
        if form.is_valid():
            form.save()
            messages.success(request, 'Link Registry updated successfully.')
            return redirect('link_registry')
        else:
            messages.error(request, form.errors.as_text())
    else:
        form = LinkRegistryForm(instance=link_registry)
    context['form'] = form
    context['link_registry'] = link_registry
    context['is_edit'] = True
    return render(request, 'link_registry/link_registry_edit.html', context)

@login_required
def link_registry_delete(request, link_registry_id):
    link_registry = LinkRegistry.objects.filter(id=link_registry_id).first()
    if link_registry:
        link_registry.delete()
        messages.success(request, 'Link Registry deleted successfully.')
    else:
        messages.error(request, 'Link Registry not found.')
    return redirect('link_registry')

# =============== INSTANCE INFO VIEWS ===============
@login_required
def instance_info(request):
    context = {}
    context["instances"] = InstanceInfo.objects.all()
    return render(request, 'link_registry/instances.html', context)

@login_required
def instance_info_add(request):
    context = {}
    if request.method == 'POST':
        form = InstanceInfoForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Instance Info added successfully.')
            return redirect('instance_info')
        else:
            messages.error(request, form.errors.as_text())
    else:
        form = InstanceInfoForm()
    context['form'] = form
    return render(request, 'link_registry/instance_info_edit.html', context)

@login_required
def instance_info_edit(request, instance_info_id):
    context = {}
    instance_info = InstanceInfo.objects.filter(id=instance_info_id).first()
    if not instance_info:
        messages.error(request, 'Instance Info not found.')
        return redirect('instance_info')
    if request.method == 'POST':
        form = InstanceInfoForm(request.POST, instance=instance_info)
        if form.is_valid():
            form.save()
            messages.success(request, 'Instance Info updated successfully.')
            return redirect('instance_info')
        else:
            messages.error(request, form.errors.as_text())
    else:
        form = InstanceInfoForm(instance=instance_info)
    context['form'] = form
    context['instance_info'] = instance_info
    context['is_edit'] = True
    return render(request, 'link_registry/instance_info_edit.html', context)

@login_required
def instance_info_delete(request, instance_info_id):
    instance_info = InstanceInfo.objects.filter(id=instance_info_id).first()
    if instance_info:
        instance_info.delete()
        messages.success(request, 'Instance Info deleted successfully.')
    else:
        messages.error(request, 'Instance Info not found.')
    return redirect('instance_info')