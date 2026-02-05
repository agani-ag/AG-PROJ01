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
