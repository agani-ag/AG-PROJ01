from django.db import transaction
from django.utils import timezone
from ..models import Project, ProjectStage, ConstructionStage


@transaction.atomic
def create_project(data):
    """
    Create project and auto-create stage breakdown.
    """

    project = Project.objects.create(**data)

    stages = ConstructionStage.objects.filter(is_active=True)

    for stage in stages:
        ProjectStage.objects.create(
            project=project,
            stage=stage,
            estimated_stage_value=0,
            expected_margin_percentage=0
        )

    return project

# Remaining Opportunity
def calculate_remaining_opportunity(project):
    stages = project.stages.all()

    remaining = 0
    for stage in stages:
        remaining += (stage.estimated_stage_value - stage.captured_stage_revenue)

    return remaining

# Update Lead Status
def update_lead_status(project, new_status):
    project.lead_status = new_status
    project.save(update_fields=["lead_status"])
