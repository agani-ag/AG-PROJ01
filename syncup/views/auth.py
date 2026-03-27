# Django imports
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth import login, logout, authenticate

# Imports
from ..forms import (
    UserProfileForm, SignupForm,
    AuthForm
)

# Python imports
import base64

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
            userprofile.encoded_credentials = base64.b64encode(f"{user.username}:{signup_form.cleaned_data['password1']}".encode()).decode()
            userprofile.save()
            messages.success(request, "User created successfully!")
            return redirect("profiles")
            # login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        else:
            user.delete()  # Rollback user creation if profile is invalid
            messages.error(request, f"{profile_form.errors}")
            return render(request, 'auth/signup.html', context)
    return render(request, 'auth/signup.html', context)

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