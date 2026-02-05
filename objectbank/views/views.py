from django.shortcuts import render
from django.http import HttpResponse
from django.contrib import messages

def home(request):
    messages.success(request, "Welcome to AG-PROJ01! 🎉")
    return render(request, 'base.html')

# objectbank/views.py
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticatedOrReadOnly
from rest_framework.response import Response

# Public read-only API
@api_view(['GET'])
@permission_classes([AllowAny])
def public_api(request):
    return Response({"message": "Anyone can see this"})

# Default behavior: read for all, write for authenticated
@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticatedOrReadOnly])
def items_api(request):
    if request.method == 'GET':
        return Response({"items": ["apple", "banana"]})
    elif request.method == 'POST':
        return Response({"status": "Created by " + str(request.user.username)})