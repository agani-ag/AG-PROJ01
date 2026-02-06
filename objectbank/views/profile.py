# Django imports
from django.contrib import messages
from django.http import HttpResponse
from django.contrib.auth import login, logout
from django.shortcuts import (
    render, redirect, get_object_or_404
)
from django.contrib.auth.models import User
# Imports
from ..forms import (
    UserProfileEditForm
)
from ..models import (
    UserProfile
)

# =============== AUTH VIEWS ===============
def profiles(request):
    context = {}
    user_profiles = UserProfile.objects.all()
    context["user_profiles"] = user_profiles
    return render(request, 'profile/profiles.html', context)

def profile_edit(request):
    context = {}
    user_profile = get_object_or_404(UserProfile, user=request.user)
    profile_form = UserProfileEditForm(instance=user_profile)
    if request.method == "POST":
        profile_form = UserProfileEditForm(request.POST, instance=user_profile)
        if profile_form.is_valid():
            profile_form.save()
            messages.success(request, "Profile updated successfully!")
        else:
            messages.error(request, f"{profile_form.errors}")
    context["profile_form"] = profile_form
    return render(request, 'profile/profile_edit.html', context)

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
            auth_user.save()
            messages.success(request, "Profile & permissions updated successfully!")
        else:
            messages.error(request, f"{profile_form.errors}")
    context["profile_form"] = profile_form
    context["auth_user"] = auth_user
    return render(request, 'profile/profile_edit.html', context)

def profile_delete(request, user_id):
    if request.method == "POST":
        user = get_object_or_404(User, id=user_id)
        user.delete()
        messages.success(request, "User deleted successfully!")
    return redirect('profiles')