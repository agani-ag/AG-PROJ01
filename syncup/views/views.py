import requests
from django.shortcuts import render
from django.contrib import messages
from django.http.response import JsonResponse

def home(request):
    return render(request, 'home.html')