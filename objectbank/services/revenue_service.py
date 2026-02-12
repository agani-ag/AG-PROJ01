from django.db import transaction
from ..models import ProjectRevenueTransaction, ProjectStage
from django.db import models

# Record Revenue Transaction
@transaction.atomic
def record_transaction(data):
    transaction = ProjectRevenueTransaction.objects.create(**data)

    # Update stage captured revenue
    stage = ProjectStage.objects.get(
        project=transaction.project,
        stage=transaction.stage
    )

    stage.captured_stage_revenue += transaction.revenue_amount
    stage.save(update_fields=["captured_stage_revenue"])

    return transaction

# Capture Ratio
def calculate_capture_ratio(project):
    total_estimated = project.estimated_total_value

    total_captured = project.revenue_transactions.aggregate(
        total=models.Sum("revenue_amount")
    )["total"] or 0

    if total_estimated == 0:
        return 0

    return (total_captured / total_estimated) * 100
