from django.shortcuts import render
from django.http import HttpResponse
from django.contrib import messages

def home(request):
    messages.success(request, "Welcome to AG-PROJ01! 🎉")
    return render(request, 'base.html')