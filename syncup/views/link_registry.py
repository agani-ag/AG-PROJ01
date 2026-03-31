# Django imports
from django.db.models import F
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required

from ..models import (
    LinkRegistry,
    UserProfile
)
from ..forms import (
    LinkRegistryForm
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