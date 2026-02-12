from django.db.models import Sum
from ..models import Worker, ProjectRevenueTransaction

# Calculate Worker Revenue
def calculate_worker_revenue(worker):
    result = ProjectRevenueTransaction.objects.filter(
        worker=worker
    ).aggregate(total=Sum("revenue_amount"))

    return result["total"] or 0

# Loyalty Score
def calculate_loyalty_score(worker):
    total_projects = worker.worker_projects.count()
    referred_projects = worker.referred_projects.count()

    if total_projects == 0:
        return 0

    return (referred_projects / total_projects) * 100
