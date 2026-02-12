from django.db import transaction
from django.utils import timezone
from ..models import Worker, WorkerAssignment

# Match Worker (Rule-Based)
def match_workers(requirement):
    return Worker.objects.filter(
        role=requirement.role,
        primary_pincode=requirement.project.pincode,
        active_status=True
    )

# Assign Worker
@transaction.atomic
def assign_worker(requirement, worker):
    assignment = WorkerAssignment.objects.create(
        requirement=requirement,
        worker=worker,
        assigned_date=timezone.now().date()
    )

    requirement.status_id = 2  # ASSIGNED
    requirement.save(update_fields=["status"])

    return assignment