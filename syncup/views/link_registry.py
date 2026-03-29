# Django imports
from django.db.models import F
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required

from ..models import (
    LinkRegistry
)
from ..forms import (
    LinkRegistryForm
)

# =============== LINK REGISTRY VIEWS ===============
@login_required
def link_registry(request):
    context = {}
    user_filter = request.GET.get('filter')
    public_filter = request.GET.get('public')
    queryset = LinkRegistry.objects.all()
    if public_filter == 'true':
        queryset = queryset.filter(is_public=True)
        context["users"] = queryset.values_list(
            'public_user', flat=True
        ).distinct().exclude(public_user__isnull=True).exclude(public_user__exact='').order_by('public_user')
        if user_filter:
            queryset = queryset.filter(public_user=user_filter)
        is_public = True
        queryset = queryset.order_by('public_user')
    else:
        queryset = queryset.filter(is_public=False)
        context["users"] = queryset.values_list(
            'user__name', flat=True
        ).distinct().order_by('user__name')
        if user_filter:
            queryset = queryset.filter(user__name=user_filter)
        is_public = False
        queryset = queryset.order_by('user__name', 'name')
    context["link_registries"] = queryset
    context["selected_user"] = user_filter
    context["public_filter"] = is_public
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