from django.shortcuts import render
from django.http import HttpResponse
from django.contrib import messages

def home(request):
    messages.success(request, "Welcome to AG-PROJ01! 🎉")
    return render(request, 'home.html')

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
    
# Leads Management

# Dashboard View
from django.shortcuts import render
from ..services.analytics_service import revenue_per_pincode
from ..models import Project


def dashboard_view(request):
    total_projects = Project.objects.count()
    pincode_data = revenue_per_pincode()

    context = {
        "total_projects": total_projects,
        "pincode_data": pincode_data
    }

    return render(request, "dashboard.html", context)

def project_list_view(request):
    projects = Project.objects.select_related(
        "current_stage", "lead_status"
    ).all()

    return render(request, "project_list.html", {"projects": projects})

# Project Create
from django.shortcuts import redirect
from ..forms import ProjectForm, RequirementForm, RevenueForm, WorkerForm
from ..services.project_service import create_project


def project_create_view(request):
    if request.method == "POST":
        form = ProjectForm(request.POST)
        if form.is_valid():
            create_project(form.cleaned_data)
            return redirect("project_list")
    else:
        form = ProjectForm()

    return render(request, "project_create.html", {"form": form})

# Project Detail
from ..services.project_service import calculate_remaining_opportunity


def project_detail_view(request, pk):
    project = Project.objects.get(pk=pk)
    remaining = calculate_remaining_opportunity(project)

    return render(request, "project_detail.html", {
        "project": project,
        "remaining": remaining
    })

# Worker Create
def worker_create_view(request):
    if request.method == "POST":
        form = WorkerForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("worker_list")
    else:
        form = WorkerForm()

    return render(request, "worker_create.html", {"form": form})

#  Revenue Create
from ..services.revenue_service import record_transaction


def revenue_create_view(request):
    if request.method == "POST":
        form = RevenueForm(request.POST)
        if form.is_valid():
            record_transaction(form.cleaned_data)
            return redirect("project_list")
    else:
        form = RevenueForm()

    return render(request, "revenue_create.html", {"form": form})

# Requirement Create
def requirement_create_view(request):
    if request.method == "POST":
        form = RequirementForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("project_list")
    else:
        form = RequirementForm()

    return render(request, "requirement_create.html", {"form": form})

# 