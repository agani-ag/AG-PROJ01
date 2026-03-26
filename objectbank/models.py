from django.db import models
from datetime import timedelta
from django.utils.timezone import now
from django.contrib.auth.models import User
from multiselectfield import MultiSelectField
from .utils import (
    phone_validator, pincode_validator
)

DAYS_OF_WEEK = (
    ('MON', 'Monday'),
    ('TUE', 'Tuesday'),
    ('WED', 'Wednesday'),
    ('THU', 'Thursday'),
    ('FRI', 'Friday'),
    ('SAT', 'Saturday'),
    ('SUN', 'Sunday'),
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
    
    # Salary & Working Days
    salary = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    working_days = MultiSelectField(choices=DAYS_OF_WEEK, max_choices=7, blank=True, null=True)

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

# =============== Holiday & Attendance ===============
class Holiday(models.Model):
    date = models.DateField(unique=True)
    description = models.CharField(max_length=255)

    def __str__(self):
        return f"{self.date} - {self.description}"

class Attendance(models.Model):
    user = models.ForeignKey(UserProfile, on_delete=models.CASCADE)
    date = models.DateField()
    present = models.BooleanField(default=False)

    class Meta:
        unique_together = ('user', 'date')

    def __str__(self):
        return f"{self.user.name} - {self.date} - {'Present' if self.present else 'Absent'}"

class SalaryTransaction(models.Model):
    user = models.ForeignKey(UserProfile, on_delete=models.CASCADE)
    month = models.IntegerField()
    year = models.IntegerField()
    base_salary = models.DecimalField(max_digits=10, decimal_places=2)
    credits = models.DecimalField(max_digits=10, decimal_places=2, default=0.0)
    bonus = models.DecimalField(max_digits=10, decimal_places=2, default=0.0)
    calculated_salary = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        unique_together = ('user', 'month', 'year')

    def __str__(self):
        return f"{self.user.name} - {self.month}/{self.year} - {self.calculated_salary}"

# =============== Construction CRM ===============
class JobRole(models.Model):
    name = models.CharField(max_length=100, unique=True)
    category = models.CharField(max_length=100, blank=True, null=True)

    def save(self, *args, **kwargs):
        if self.name:
            self.name = self.name.strip().title()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name
    
    class Meta:
        ordering = ['name']

class Worker(models.Model):
    name = models.CharField(max_length=100)
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True, validators=[phone_validator])
    mobile = models.CharField(max_length=20, blank=True, null=True, validators=[phone_validator])
    location = models.CharField(max_length=100, blank=True, null=True)
    pincode = models.CharField(max_length=10, blank=True, null=True, validators=[pincode_validator])
    job_role = models.ForeignKey(JobRole, on_delete=models.SET_NULL, null=True, blank=True)
    experience_years = models.IntegerField(default=0)
    company_name = models.CharField(max_length=100, blank=True, null=True, default="SELF-EMPLOYED")
    TRUST_LEVEL = [
        (0, 'Low'),
        (1, 'Medium'),
        (2, 'High'),
    ]
    trust_level = models.IntegerField(choices=TRUST_LEVEL, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    def save(self, *args, **kwargs):
        if self.name:
            self.name = self.name.strip().title()
        if self.location:
            self.location = self.location.strip().title()
        if self.email:
            self.email = self.email.strip().lower()
        if self.company_name:
            self.company_name = self.company_name.strip().title()
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"{self.name} - {self.job_role}"

class Leads(models.Model):
    name = models.CharField(max_length=100)
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True, validators=[phone_validator])
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    location = models.TextField(max_length=100, blank=True, null=True)
    pincode = models.CharField(max_length=10, blank=True, null=True, validators=[pincode_validator])
    TYPE = [
        (0, 'New Construction'),
        (1, 'Renovation'),
        (2, 'Commercial'),
        (3, 'Residential'),
    ]
    type = models.IntegerField(choices=TYPE, default=0)
    PROJECT_STAGE = [
        (1, 'Idea'), (2, 'Budgeting'),
        (3, 'Planning'), (4, 'Design'),
        (5, 'Approval'), (6, 'Tendering'),
        (7, 'Site Preparation'), (8, 'Excavation'),
        (9, 'Foundation'), (10, 'Structure'),
        (11, 'Roofing'), (12, 'Brickwork'),
        (13, 'Plastering'), (14, 'Electrical'),
        (15, 'Plumbing'), (16, 'Flooring'),
        (17, 'Finishing'), (18, 'Interior'),
        (19, 'Inspection'), (20, 'Handover'),
        (21, 'Maintenance'),
    ]
    project_stage = models.IntegerField(choices=PROJECT_STAGE, default=0)
    SOURCE_TYPES = [
        (0, 'Direct'),
        (1, 'Referral'),
        (2, 'Online'),
        (3, 'Other'),
    ]
    source_type = models.IntegerField(choices=SOURCE_TYPES, default=0)
    STATUS = [
        (0, 'New'),
        (1, 'Contacted'),
        (2, 'Site Visit'),
        (3, 'Proposal'),
        (4, 'Negotiation'),
        (5, 'Won'),
        (6, 'Lost'),
    ]
    status = models.IntegerField(choices=STATUS, default=0)
    worker = models.ForeignKey(Worker, on_delete=models.SET_NULL, null=True, blank=True)
    assigned_to = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if self.name:
            self.name = self.name.strip().title()
        if self.location:
            self.location = self.location.strip().title()
        if self.email:
            self.email = self.email.strip().lower()
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"{self.name} - {self.get_status_display()}"
    
    class Meta:
        ordering = ['-created_at']

def default_future_date():
    return now().date() + timedelta(days=15)
    
class Opportunity(models.Model):
    lead = models.ForeignKey(Leads, on_delete=models.CASCADE)
    TYPE = [
        (0, 'Construction'),
        (1, 'Material Supply'),
        (2, 'Consulting'),
        (3, 'Maintenance'),

        # Civil & Structural
        (4, 'Renovation'),
        (5, 'Interior Work'),
        (6, 'Exterior Work'),
        (7, 'Structural Work'),

        # Services
        (8, 'Electrical Work'),
        (9, 'Plumbing Work'),
        (10, 'HVAC Work'),
        (11, 'Painting Work'),
        (12, 'Waterproofing'),

        # Specialized
        (13, 'Fabrication'),
        (14, 'Landscaping'),
        (15, 'Demolition'),

        # Design & Planning
        (16, 'Architecture Design'),
        (17, 'Structural Design'),
        (18, 'Project Management'),

        # Equipment / Rental
        (19, 'Equipment Rental'),
        (20, 'Labor Supply'),

        # Misc
        (21, 'Inspection'),
        (22, 'Surveying'),
        (23, 'Other'),
    ]
    type = models.IntegerField(choices=TYPE, default=0)
    estimated_value = models.DecimalField(max_digits=15, decimal_places=2, blank=True, null=True)
    closing_date = models.DateField(default=default_future_date)
    STATUS = [
        (0, 'Open'),
        (1, 'In Progress'),
        (2, 'Closed Won'),
        (3, 'Closed Lost'),
    ]
    status = models.IntegerField(choices=STATUS, default=0)
    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.lead.name} - {self.get_status_display()}"

class MaterialRequest(models.Model):
    lead = models.ForeignKey(Leads, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    requirement = models.TextField()
    TYPE = [
        # Civil Materials
        (0, 'Cement & Concrete'),
        (1, 'Sand & Aggregates'),
        (2, 'Bricks & Blocks'),
        (3, 'Steel & Reinforcement'),

        # Electrical
        (4, 'Wiring & Cables'),
        (5, 'Switches & Fixtures'),
        (6, 'Lighting'),
        (7, 'Electrical Panels'),

        # Plumbing
        (8, 'Pipes & Fittings'),
        (9, 'Sanitary Ware'),
        (10, 'Bathroom Fixtures'),

        # Finishing
        (11, 'Paint & Coatings'),
        (12, 'Tiles & Flooring'),
        (13, 'False Ceiling'),
        (14, 'Glass & Aluminium'),

        # Woodwork
        (15, 'Plywood & Boards'),
        (16, 'Timber'),
        (17, 'Furniture & Fittings'),

        # Metalwork
        (18, 'Structural Steel'),
        (19, 'Fabrication Materials'),

        # Specialized
        (20, 'Waterproofing Materials'),
        (21, 'HVAC Materials'),
        (22, 'Insulation Materials'),

        # Site & Misc
        (23, 'Hardware & Fasteners'),
        (24, 'Safety Equipment'),
        (25, 'Tools & Equipment'),
        (26, 'Chemicals & Adhesives'),

        # Other
        (27, 'Other'),
    ]
    type = models.IntegerField(choices=TYPE, default=0)
    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    STATUS = [
        (0, 'Open'),
        (1, 'In Progress'),
        (2, 'Fulfilled'),
        (3, 'Cancelled'),
    ]
    status = models.IntegerField(choices=STATUS, default=0)

    def save(self, *args, **kwargs):
        if self.name:
            self.name = self.name.strip().title()
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"{self.name} - {self.get_status_display()}"

class ActivityLog(models.Model):
    lead = models.ForeignKey(Leads, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    TYPE = [
        (0, 'Communication'),
        (1, 'Site Visit'),
        (2, 'Proposal Sent'),
        (3, 'Negotiation'),
        (4, 'Follow-up'),
        (5, 'Other'),
    ]
    type = models.IntegerField(choices=TYPE, default=0)
    description = models.TextField(blank=True, null=True)
    follow_up_date = models.DateField(blank=True, null=True, default=default_future_date)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.get_type_display()} - {self.lead.name} - {self.created_at.strftime('%Y-%m-%d %H:%M:%S')}"

class WorkerCommissions(models.Model):
    worker = models.ForeignKey(Worker, on_delete=models.CASCADE)
    lead = models.ForeignKey(Leads, on_delete=models.CASCADE)
    opportunity = models.ForeignKey(Opportunity, on_delete=models.CASCADE)
    commission_amount = models.DecimalField(max_digits=10, decimal_places=2)
    paid = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.worker.name} - {self.lead.name} - {self.opportunity.get_type_display()} - {self.commission_amount}"