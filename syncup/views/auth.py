# Django imports
from django.conf import settings
from django.contrib import messages
from django.shortcuts import render, redirect
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login, logout, authenticate

# Imports
from ..forms import (
    UserProfileForm, SignupForm,
    AuthForm
)
from syncup.models import UserProfile
from django.contrib.auth.models import User

# Python imports
from datetime import datetime
import secrets
import base64
import os

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

@login_required
def signup_view(request):
    context = {}
    signup_form = SignupForm()
    profile_form = UserProfileForm()
    context["signup_form"] = signup_form
    context["profile_form"] = profile_form
    
    if request.method == "POST":
        random_password = secrets.token_urlsafe(8)
        signup_form = SignupForm(request.POST)
        profile_form = UserProfileForm(request.POST)
        context["signup_form"] = signup_form
        context["profile_form"] = profile_form

        if signup_form.is_valid():
            user = signup_form.save(commit=False)
            user.set_password(random_password)
            user.save()
        else:
            messages.error(request, f"{signup_form.errors}")
            return render(request, 'auth/signup.html', context)
        if profile_form.is_valid():
            userprofile = profile_form.save(commit=False)
            userprofile.user = user
            userprofile.random_password = random_password
            userprofile.encoded_credentials = base64.b64encode(f"{user.username}:{random_password}".encode()).decode()
            userprofile.save()
            messages.success(request, "User created successfully!")
            return redirect("profiles")
            # login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        else:
            user.delete()  # Rollback user creation if profile is invalid
            messages.error(request, f"{profile_form.errors}")
            return render(request, 'auth/signup.html', context)
    return render(request, 'auth/signup.html', context)

@login_required
def logout_view(request):
    logout(request)
    return redirect('login')

# =============== API VIEWS ===============
def auth_login_api(request):
    data_encoded = request.GET.get("data")
    if not data_encoded:
        return JsonResponse({"error": "Missing credentials"}, status=400)
    try:
        decoded_str = base64.b64decode(data_encoded).decode("utf-8")
        username, password = decoded_str.split(":", 1)
    except Exception:
        return JsonResponse({"error": "Invalid credentials format"}, status=400)
    if not username or not password:
        return JsonResponse({"error": "Username and password are required"}, status=400)
    user = authenticate(request, username=username, password=password)
    if user:
        login(request, user)
        return redirect("home")
        # return JsonResponse({"message": "Login successful", "username": user.username})
    else:
        return JsonResponse({"error": "Invalid username or password"}, status=401)

@login_required
def reset_password_api(request):
    user_id = request.GET.get("user_id")
    password = request.GET.get("password")
    if not user_id:
        return JsonResponse({"error": "Missing user ID"}, status=400)
    try:
        user = User.objects.get(id=user_id)
        userprofile = UserProfile.objects.get(user=user)
        new_password = password if password else secrets.token_urlsafe(8)
        user.set_password(new_password)
        user.save()
        userprofile.random_password = new_password
        userprofile.encoded_credentials = base64.b64encode(f"{user.username}:{new_password}".encode()).decode()
        userprofile.save()
        return JsonResponse({"message": "Password reset successful", "new_password": new_password})
    except User.DoesNotExist:
        return JsonResponse({"error": "User not found"}, status=404)

def download_sqlite(request):
    sqlite_path = os.path.join(settings.BASE_DIR, 'ag-proj01.sqlite3')
    if os.path.exists(sqlite_path):
        with open(sqlite_path, 'rb') as f:
            response = HttpResponse(f.read(), content_type='application/octet-stream')
            filename = datetime.now().strftime("syncup_%Y%m%d_%H%M%S.sqlite3")
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            return response
    else:
        return HttpResponse("Database file not found", status=404)