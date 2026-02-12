from django import forms
from django.forms import ModelForm
from django.contrib.auth.models import User
from django.contrib.auth.forms import (
    UserCreationForm, AuthenticationForm
)
from .models import (
    Project, UserProfile, Worker,
    ProjectRevenueTransaction, ProjectWorkerRequirement
)

class AuthForm(AuthenticationForm):
    class Meta:
        model = User
        fields = ('username', 'password')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'form-control'})
class SignupForm(UserCreationForm):
    class Meta:
        model = User
        fields = ('username', 'password1', 'password2')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'form-control'})


class UserProfileForm(ModelForm):
    def __init__(self, *args, **kwargs):
        super(UserProfileForm, self).__init__(*args, **kwargs)
        self.fields['name'].required = True

    class Meta:
        model = UserProfile
        fields = ['name', 'dob', 'email', 'phone', 'address', 'pincode']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'pincode': forms.TextInput(attrs={'class': 'form-control'}),
            'dob': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        }

class UserProfileEditForm(ModelForm):
    class Meta:
        model = UserProfile
        fields = ['name', 'dob', 'email', 'phone', 'address', 'pincode', 'latitude', 'longitude']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'pincode': forms.TextInput(attrs={'class': 'form-control'}),
            'latitude': forms.NumberInput(attrs={'class': 'form-control'}),
            'longitude': forms.NumberInput(attrs={'class': 'form-control'}),
            'dob': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        }

# Project Forms
class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = [
            "name",
            "client_name",
            "phone",
            "full_address",
            "pincode",
            "city",
            "latitude",
            "longitude",
            "estimated_total_value",
            "current_stage",
            "lead_status",
            "expected_completion_date"
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'client_name': forms.TextInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'full_address': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'pincode': forms.TextInput(attrs={'class': 'form-control'}),
            'city': forms.TextInput(attrs={'class': 'form-control'}),
            'latitude': forms.NumberInput(attrs={'class': 'form-control'}),
            'longitude': forms.NumberInput(attrs={'class': 'form-control'}),
            'estimated_total_value': forms.NumberInput(attrs={'class': 'form-control'}),
            'current_stage': forms.Select(attrs={'class': 'form-control'}),
            'lead_status': forms.Select(attrs={'class': 'form-control'}),
            'expected_completion_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        }


class WorkerForm(forms.ModelForm):
    class Meta:
        model = Worker
        fields = [
            "name",
            "role",
            "phone",
            "primary_pincode",
            "joined_date"
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter worker name'}),
            'role': forms.Select(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '10-digit mobile number'}),
            'primary_pincode': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '6-digit pincode'}),
            'joined_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        }


class RevenueForm(forms.ModelForm):
    class Meta:
        model = ProjectRevenueTransaction
        fields = [
            "project",
            "stage",
            "worker",
            "invoice_number",
            "revenue_amount",
            "cost_amount",
            "margin_amount",
            "transaction_date"
        ]
        widgets = {
            'project': forms.Select(attrs={'class': 'form-control'}),
            'stage': forms.Select(attrs={'class': 'form-control'}),
            'worker': forms.Select(attrs={'class': 'form-control'}),
            'invoice_number': forms.TextInput(attrs={'class': 'form-control'}),
            'revenue_amount': forms.NumberInput(attrs={'class': 'form-control'}),
            'cost_amount': forms.NumberInput(attrs={'class': 'form-control'}),
            'margin_amount': forms.NumberInput(attrs={'class': 'form-control'}),
            'transaction_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        }


class RequirementForm(forms.ModelForm):
    class Meta:
        model = ProjectWorkerRequirement
        fields = [
            "project",
            "role",
            "required_from_date",
            "urgency",
            "status"
        ]
        widgets = {
            'project': forms.Select(attrs={'class': 'form-control'}),
            'role': forms.Select(attrs={'class': 'form-control'}),
            'required_from_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'urgency': forms.Select(attrs={'class': 'form-control'}),
            'status': forms.Select(attrs={'class': 'form-control'}),
        }
