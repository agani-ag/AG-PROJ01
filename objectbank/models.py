from django.db import models
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