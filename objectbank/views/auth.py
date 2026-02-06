# Django imports
from django.contrib import messages
from django.contrib.auth import login, logout
from django.shortcuts import render, redirect
from django.contrib.auth.models import User
from rest_framework.decorators import api_view
from rest_framework.response import Response

# Imports
from ..forms import (
    UserProfileForm, SignupForm,
    AuthForm
)
from ..models import UserProfile

# =============== AUTH VIEWS ===============
def login_view(request):
    if request.user.is_authenticated:
        return redirect("home")
    context = {}
    auth_form = AuthForm(request)
    if request.method == "POST":
        auth_form = AuthForm(request, data=request.POST)
        if auth_form.is_valid():
            user = auth_form.get_user()
            if user:
                login(request, user)
                return redirect("home")
        else:
            messages.error(request, auth_form.get_invalid_login_error())
    context["auth_form"] = auth_form
    return render(request, 'auth/login.html', context)

def signup_view(request):
    admin = request.GET.get("admin", None)
    if request.user.is_authenticated and not admin:
        return redirect("home")
    context = {}
    signup_form = SignupForm()
    profile_form = UserProfileForm()
    context["signup_form"] = signup_form
    context["profile_form"] = profile_form
    
    if request.method == "POST":
        signup_form = SignupForm(request.POST)
        profile_form = UserProfileForm(request.POST)
        context["signup_form"] = signup_form
        context["profile_form"] = profile_form

        if signup_form.is_valid():
            user = signup_form.save()
        else:
            messages.error(request, f"{signup_form.errors}")
            return render(request, 'auth/signup.html', context)
        if profile_form.is_valid():
            userprofile = profile_form.save(commit=False)
            userprofile.user = user
            userprofile.save()
            if request.user.is_authenticated:
                messages.success(request, "User created successfully!")
                return redirect("profiles")
            login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            return redirect("home")
        else:
            user.delete()  # Rollback user creation if profile is invalid
            messages.error(request, f"{profile_form.errors}")
            return render(request, 'auth/signup.html', context)

    return render(request, 'auth/signup.html', context)

def logout_view(request):
    logout(request)
    return redirect('login')

# =============== REST API VIEWS ===============
@api_view(['GET'])
def user_list(request):
    users = User.objects.filter(is_active=True).values('id', 'username')
    return Response(list(users))