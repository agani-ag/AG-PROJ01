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

class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = [
            "project_code",
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


class WorkerForm(forms.ModelForm):
    class Meta:
        model = Worker
        fields = [
            "worker_code",
            "name",
            "role",
            "phone",
            "primary_pincode",
            "joined_date"
        ]


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
