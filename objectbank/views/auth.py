# Django imports
from django.contrib.auth import login, logout
from django.http import HttpResponse
from django.contrib import messages
from django.shortcuts import render, redirect
from django.contrib.auth.forms import (
    AuthenticationForm, UserCreationForm
)

# Forms
from ..forms import (
    UserProfileForm, SignupForm
)
# Python imports
from datetime import date

def signup_view(request):
    # if request.user.is_authenticated:
    #     return redirect("invoice_create")
    context = {}
    signup_form = SignupForm()
    profile_form = UserProfileForm()
    context["signup_form"] = signup_form
    context["profile_form"] = profile_form
    context["today"] = date.today().isoformat()
    
    if request.method == "POST":
        signup_form = SignupForm(request.POST)
        profile_form = UserProfileForm(request.POST)
        context["signup_form"] = signup_form
        context["profile_form"] = profile_form

        if signup_form.is_valid():
            user = signup_form.save()
        else:
            messages.error(request, f"{signup_form.errors}")
            context["error_message"] = signup_form.errors
            return render(request, 'auth/signup.html', context)
        if profile_form.is_valid():
            userprofile = profile_form.save(commit=False)
            userprofile.user = user
            userprofile.save()
            login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            return HttpResponse("Signup successful! Welcome, " + user.username)
        else:
            user.delete()  # Rollback user creation if profile is invalid
            messages.error(request, f"{profile_form.errors}")
            return render(request, 'auth/signup.html', context)

    return render(request, 'auth/signup.html', context)