# Django imports
import json
from django.contrib import messages
from django.http import JsonResponse, HttpResponseForbidden
from django.urls import reverse
from django.shortcuts import (
    render, redirect, get_object_or_404
)
from django.contrib.auth.models import User
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required

# Imports
from ..forms import (
    PublicUserForm,
    PublicUserSelfForm,
    UserProfileEditForm,
    UserSelfProfileForm,
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
    profile_form = UserSelfProfileForm(instance=user_profile)
    auth_user = request.user
    if request.method == "POST":
        profile_form = UserSelfProfileForm(request.POST, instance=user_profile)
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
    if not request.user.is_superuser:
        messages.error(request, "You are not authorized to edit other users.")
        return redirect('profile_edit')
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
    if not request.user.is_superuser:
        messages.error(request, "You are not authorized to manage public users.")
        return redirect('profile_edit')
    context = {}
    context["public_users"] = PublicUser.objects.all()
    return render(request, 'profile/public_users.html', context)

@login_required
def public_user_add(request):
    if not request.user.is_superuser:
        messages.error(request, "You are not authorized to add public users.")
        return redirect('profile_edit')
    context = {}
    if request.method == 'POST':
        form = PublicUserForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Public User added successfully.')
            return redirect('public_users')
        else:
            messages.error(request, form.errors.as_text())
    else:
        form = PublicUserForm()
    context['form'] = form
    context['is_admin'] = True
    return render(request, 'profile/public_user_edit.html', context)

def public_user_edit(request, public_user_id):
    """Superusers edit any public user fully. A public (non-logged-in) user may
    edit ONLY their own record — proven by the ?token= from their login link —
    and only safe fields + their URLs (never credentials)."""
    public_user = PublicUser.objects.filter(id=public_user_id).first()
    if not public_user:
        return HttpResponseForbidden("Public user not found.")

    is_admin = request.user.is_authenticated and request.user.is_superuser
    token = request.GET.get('token') or request.POST.get('token') or ''
    token_ok = bool(token) and str(public_user.edit_token) == str(token)
    if not (is_admin or token_ok):
        return HttpResponseForbidden("You are not authorized to edit this record.")

    FormClass = PublicUserForm if is_admin else PublicUserSelfForm
    if request.method == 'POST':
        form = FormClass(request.POST, instance=public_user)
        if form.is_valid():
            obj = form.save(commit=False)
            urls_data = request.POST.get('urls')
            if urls_data is not None:
                try:
                    obj.urls = json.loads(urls_data) if urls_data else {}
                except (ValueError, TypeError):
                    pass  # malformed → keep existing urls
            obj.save()
            messages.success(request, 'Saved successfully.')
            if is_admin:
                return redirect('public_users')
            # Public self-edit → reload their own page (keep the token).
            return redirect(reverse('public_user_edit', args=[public_user.id]) + f'?token={public_user.edit_token}')
        else:
            messages.error(request, form.errors.as_text())
    else:
        form = FormClass(instance=public_user)

    context = {
        'form': form,
        'public_user': public_user,
        'is_edit': True,
        'is_admin': is_admin,
        'token': token if token_ok else '',
    }
    return render(request, 'profile/public_user_edit.html', context)

@login_required
@require_POST
def public_user_delete(request, public_user_id):
    if not request.user.is_superuser:
        messages.error(request, "You are not authorized to delete public users.")
        return redirect('profile_edit')
    public_user = PublicUser.objects.filter(id=public_user_id).first()
    if public_user:
        public_user.delete()
        messages.success(request, 'Public User deleted successfully.')
    else:
        messages.error(request, 'Public User not found.')
    return redirect('public_users')
