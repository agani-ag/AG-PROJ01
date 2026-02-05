from django.shortcuts import render
from django.http import HttpResponse

def home(request):
    return HttpResponse("AG-PROJ01 is running successfully!")