from django.db import models
from django.contrib.auth.models import User
from .utils import (
    phone_validator, pincode_validator
)

# =============== UserProfile ===============
class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)

    # Personal Info
    name = models.CharField(max_length=100, blank=True, null=True)
    dob = models.DateField(blank=True, null=True)    
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True, validators=[phone_validator])
    address = models.TextField(max_length=400, blank=True, null=True)
    pincode = models.CharField(max_length=10, blank=True, null=True, validators=[pincode_validator])
    
    # Geolocation
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if self.name:
            self.name = self.name.strip().upper()
        if self.address:
            self.address = self.address.strip().upper()
        if self.email:
            self.email = self.email.strip().lower()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name or self.user.username
    
# =============== Link Registry ===============
class LinkRegistry(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    link_name = models.CharField(max_length=100)
    link_url = models.URLField(max_length=300)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    active = models.BooleanField(default=True)

    def save(self, *args, **kwargs):
        if self.link_name:
            self.link_name = self.link_name.strip().upper()
        if self.link_url:
            self.link_url = self.link_url.strip()
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"{self.link_name} - {self.user.username}"

# =====================================================
# MASTER TABLES
# =====================================================

class ConstructionStage(models.Model):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=50)
    sequence_order = models.PositiveIntegerField(unique=True)
    description = models.TextField(blank=True, null=True)
    default_margin_priority = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sequence_order"]

    def __str__(self):
        return self.name


class LeadStatus(models.Model):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=50)
    sequence_order = models.PositiveIntegerField(unique=True)
    is_final = models.BooleanField(default=False)
    is_won = models.BooleanField(default=False)
    is_lost = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sequence_order"]

    def __str__(self):
        return self.name


class WorkerRole(models.Model):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=50)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class RequirementStatus(models.Model):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=50)

    def __str__(self):
        return self.name


class UrgencyLevel(models.Model):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=50)
    priority_score = models.PositiveIntegerField(default=1)

    def __str__(self):
        return self.name


class CreditTransactionType(models.Model):
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=100)

    def __str__(self):
        return self.name


# =====================================================
# PROJECT MODULE
# =====================================================

class Project(models.Model):
    project_code = models.CharField(max_length=20, unique=True)

    name = models.CharField(max_length=150)
    client_name = models.CharField(max_length=100)
    phone = models.CharField(max_length=20)

    full_address = models.TextField()
    pincode = models.CharField(max_length=10, db_index=True)
    city = models.CharField(max_length=100)

    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    estimated_total_value = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    current_stage = models.ForeignKey(
        ConstructionStage,
        on_delete=models.PROTECT,
        related_name="projects",
        db_index=True
    )

    lead_status = models.ForeignKey(
        LeadStatus,
        on_delete=models.PROTECT,
        related_name="projects",
        db_index=True
    )

    referred_by_worker = models.ForeignKey(
        "Worker",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="referred_projects"
    )

    expected_completion_date = models.DateField(null=True, blank=True)

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.project_code} - {self.name}"


class ProjectStage(models.Model):
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="stages"
    )

    stage = models.ForeignKey(
        ConstructionStage,
        on_delete=models.PROTECT
    )

    estimated_stage_value = models.DecimalField(max_digits=15, decimal_places=2)
    captured_stage_revenue = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    expected_margin_percentage = models.DecimalField(max_digits=5, decimal_places=2)

    is_completed = models.BooleanField(default=False)
    completed_date = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("project", "stage")

    def __str__(self):
        return f"{self.project.name} - {self.stage.name}"


# =====================================================
# WORKER MODULE
# =====================================================

class Worker(models.Model):
    worker_code = models.CharField(max_length=20, unique=True)

    name = models.CharField(max_length=100)

    role = models.ForeignKey(
        WorkerRole,
        on_delete=models.PROTECT,
        related_name="workers"
    )

    phone = models.CharField(max_length=20)
    primary_pincode = models.CharField(max_length=10, db_index=True)

    active_status = models.BooleanField(default=True)
    joined_date = models.DateField()

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class WorkerProject(models.Model):
    worker = models.ForeignKey(
        Worker,
        on_delete=models.CASCADE,
        related_name="worker_projects"
    )

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="worker_projects"
    )

    role = models.ForeignKey(
        WorkerRole,
        on_delete=models.PROTECT
    )

    revenue_generated = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    referred_by_worker = models.ForeignKey(
        Worker,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="referral_links"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("worker", "project")

    def __str__(self):
        return f"{self.worker.name} - {self.project.name}"


# =====================================================
# WORKER DEMAND ENGINE
# =====================================================

class ProjectWorkerRequirement(models.Model):
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="worker_requirements"
    )

    role = models.ForeignKey(
        WorkerRole,
        on_delete=models.PROTECT
    )

    required_from_date = models.DateField()

    urgency = models.ForeignKey(
        UrgencyLevel,
        on_delete=models.PROTECT
    )

    status = models.ForeignKey(
        RequirementStatus,
        on_delete=models.PROTECT
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.project.name} - {self.role.name}"


class WorkerAssignment(models.Model):
    requirement = models.OneToOneField(
        ProjectWorkerRequirement,
        on_delete=models.CASCADE,
        related_name="assignment"
    )

    worker = models.ForeignKey(
        Worker,
        on_delete=models.PROTECT,
        related_name="assignments"
    )

    assigned_date = models.DateField()
    completion_date = models.DateField(null=True, blank=True)

    revenue_impact = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.worker.name} assigned"


# =====================================================
# REVENUE MODULE
# =====================================================

class ProjectRevenueTransaction(models.Model):
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="revenue_transactions"
    )

    stage = models.ForeignKey(
        ConstructionStage,
        on_delete=models.PROTECT
    )

    worker = models.ForeignKey(
        Worker,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    invoice_number = models.CharField(max_length=50)

    revenue_amount = models.DecimalField(max_digits=15, decimal_places=2)
    cost_amount = models.DecimalField(max_digits=15, decimal_places=2)
    margin_amount = models.DecimalField(max_digits=15, decimal_places=2)

    transaction_date = models.DateField(db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.project.project_code} - {self.revenue_amount}"


# =====================================================
# CREDIT MODULE
# =====================================================

class WorkerCreditLedger(models.Model):
    worker = models.ForeignKey(
        Worker,
        on_delete=models.CASCADE,
        related_name="credit_entries"
    )

    project = models.ForeignKey(
        Project,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    transaction_type = models.ForeignKey(
        CreditTransactionType,
        on_delete=models.PROTECT
    )

    debit = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    running_balance = models.DecimalField(max_digits=15, decimal_places=2)

    due_date = models.DateField(null=True, blank=True)
    is_settled = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.worker.name} - {self.running_balance}"