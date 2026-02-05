from django.shortcuts import render
from django.http import HttpResponse
from django.contrib import messages

def home(request):
    messages.success(request, "Welcome to AG-PROJ01! 🎉")
    return render(request, 'base.html')

# objectbank/views.py
from rest_framework.decorators import api_view
from rest_framework.response import Response

@api_view(['GET'])
def api_root(request):
    return Response({
        "status": "API is running",
        "app": "objectbank"
    })
