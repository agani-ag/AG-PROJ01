# views.py
from ..models import LinkRegistry
from django.shortcuts import render

def link_registry_view(request):
    return render(request, 'link_registry/link_registry.html')