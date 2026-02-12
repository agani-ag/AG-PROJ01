from django.db.models import Sum, Count
from ..models import Project, ProjectRevenueTransaction

# Revenue Per Pincode
def revenue_per_pincode():
    return ProjectRevenueTransaction.objects.values(
        "project__pincode"
    ).annotate(
        total_revenue=Sum("revenue_amount")
    )

# Top 20% Workers
def top_workers():
    return ProjectRevenueTransaction.objects.values(
        "worker__id",
        "worker__name"
    ).annotate(
        total=Sum("revenue_amount")
    ).order_by("-total")
