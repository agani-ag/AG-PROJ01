# Django imports
import json
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import (
    render, redirect, get_object_or_404
)
from django.contrib.auth.models import User
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required

# Imports
from ..forms import (
    PublicUserForm,
    UserProfileEditForm
)
from ..models import (
    PublicUser, UserProfile, InvoiceEmployeeMapping
)

# =============== AUTH VIEWS ===============
@login_required
def profiles(request):
    context = {}
    user_profiles = UserProfile.objects.all()
    context["user_profiles"] = user_profiles
    return render(request, 'profile/profiles.html', context)

@login_required
def profile_edit(request):
    context = {}
    user_profile = get_object_or_404(UserProfile, user=request.user)
    profile_form = UserProfileEditForm(instance=user_profile)
    auth_user = request.user
    if request.method == "POST":
        profile_form = UserProfileEditForm(request.POST, instance=user_profile)
        if profile_form.is_valid():
            profile_form.save()
            messages.success(request, "Profile updated successfully!")
        else:
            messages.error(request, f"{profile_form.errors}")
    context["profile_form"] = profile_form
    context["auth_user"] = auth_user
    return render(request, 'profile/profile_edit.html', context)

@login_required
def admin_profile_edit(request, user_id):
    context = {}
    user_profile = get_object_or_404(UserProfile, user__id=user_id)
    profile_form = UserProfileEditForm(instance=user_profile)
    auth_user = User.objects.get(id=user_id)
    if request.method == "POST":
        profile_form = UserProfileEditForm(request.POST, instance=user_profile)
        if profile_form.is_valid():
            profile_form.save()
            auth_user.is_staff = "is_staff" in request.POST
            auth_user.is_active = "is_active" in request.POST
            auth_user.is_superuser = "is_superuser" in request.POST
            auth_user.save()
            messages.success(request, "Profile & permissions updated successfully!")
            return redirect('profiles')
        else:
            messages.error(request, f"{profile_form.errors}")
    context["profile_form"] = profile_form
    context["auth_user"] = auth_user
    context["admin_panel"] = True
    context["encoded_credentials"] = auth_user.userprofile.encoded_credentials
    return render(request, 'profile/profile_edit.html', context)

@login_required
def profile_delete(request, user_id):
    if request.method == "POST":
        user = get_object_or_404(User, id=user_id)
        user.delete()
        messages.success(request, "User deleted successfully!")
    return redirect('profiles')

# =============== PUBLIC USER VIEWS ===============
@login_required
def public_users(request):
    context = {}
    context["public_users"] = PublicUser.objects.all()
    return render(request, 'profile/public_users.html', context)

@login_required
def public_user_add(request):
    context = {}
    if request.method == 'POST':
        form =  PublicUserForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Public User added successfully.')
            return redirect('public_users')
        else:
            messages.error(request, form.errors.as_text())
    else:
        form = PublicUserForm()
    context['form'] = form
    return render(request, 'profile/public_user_edit.html', context)

# @login_required
def public_user_edit(request, public_user_id):
    context = {}
    public_user = PublicUser.objects.filter(id=public_user_id).first()
    if not public_user:
        messages.error(request, 'Public User not found.')
        return redirect('public_users')
    if request.method == 'POST':
        form = PublicUserForm(request.POST, instance=public_user)
        redirection = True
        if request.POST.get('public_user') == 'True' and not request.user.is_authenticated:
            messages.warning(request, 'The page is edited by public user.')
            redirection = False
        if form.is_valid():
            obj = form.save(commit=False)
            if redirection:
                urls_data = request.POST.get('urls')
                obj.urls = json.loads(urls_data) if urls_data else {}
            obj.save()
            messages.success(request, 'Public User updated successfully.')
            if redirection:
                return redirect('public_users')
        else:
            messages.error(request, form.errors.as_text())
    else:
        form = PublicUserForm(instance=public_user)
    context['form'] = form
    context['public_user'] = public_user
    context['is_edit'] = True
    return render(request, 'profile/public_user_edit.html', context)

@login_required
def public_user_delete(request, public_user_id):
    public_user = PublicUser.objects.filter(id=public_user_id).first()
    if public_user:
        public_user.delete()
        messages.success(request, 'Public User deleted successfully.')
    else:
        messages.error(request, 'Public User not found.')
    return redirect('public_users')
