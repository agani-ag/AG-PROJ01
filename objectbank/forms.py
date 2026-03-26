from django import forms
from datetime import date
from django.forms import ModelForm
from django.contrib.auth.models import User
from django.contrib.auth.forms import (
    UserCreationForm, AuthenticationForm
)
from .models import (
    ActivityLog, MaterialRequest, UserProfile, Worker, Leads,
    Opportunity, WorkerCommissions

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
        fields = ['name', 'dob', 'email', 'phone', 'address', 'pincode', 'salary', 'working_days']
        widgets = {
            'working_days': forms.CheckboxSelectMultiple(),
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'pincode': forms.TextInput(attrs={'class': 'form-control'}),
            'dob': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'salary': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        }

class UserProfileEditForm(ModelForm):
    class Meta:
        model = UserProfile
        fields = ['name', 'dob', 'email', 'phone', 'address', 'pincode', 'latitude', 'longitude', 'salary', 'working_days']
        widgets = {
            'working_days': forms.CheckboxSelectMultiple(),
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'pincode': forms.TextInput(attrs={'class': 'form-control'}),
            'latitude': forms.NumberInput(attrs={'class': 'form-control'}),
            'longitude': forms.NumberInput(attrs={'class': 'form-control'}),
            'dob': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'salary': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        }

class WorkerForm(ModelForm):
    class Meta:
        model = Worker
        fields = ['name', 'email', 'phone', 'mobile', 'location', 'pincode', 'job_role',
                  'experience_years', 'company_name', 'trust_level', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'mobile': forms.TextInput(attrs={'class': 'form-control'}),
            'pincode': forms.TextInput(attrs={'class': 'form-control'}),
            'location': forms.TextInput(attrs={'class': 'form-control'}),
            'trust_level': forms.Select(attrs={'class': 'form-control'}),
            'company_name': forms.TextInput(attrs={'class': 'form-control'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'experience_years': forms.NumberInput(attrs={'class': 'form-control'}),
            'job_role': forms.Select(attrs={'class': 'form-control', 'required': 'true'}),
            'phone': forms.TextInput(attrs={'class': 'form-control', 'required': 'true'}),
        }

class LeadsForm(ModelForm):
    class Meta:
        model = Leads
        fields = ['name', 'email', 'phone', 'location', 'pincode', 'latitude', 'longitude',
                  'type', 'project_stage', 'source_type', 'status', 'assigned_to', 'worker']
        widgets = {
            'type': forms.Select(attrs={'class': 'form-control'}),
            'worker': forms.Select(attrs={'class': 'form-control'}),
            'status': forms.Select(attrs={'class': 'form-control'}),
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'pincode': forms.TextInput(attrs={'class': 'form-control'}),
            'source_type': forms.Select(attrs={'class': 'form-control'}),
            'assigned_to': forms.Select(attrs={'class': 'form-control'}),
            'latitude': forms.NumberInput(attrs={'class': 'form-control'}),
            'project_stage': forms.Select(attrs={'class': 'form-control'}),
            'longitude': forms.NumberInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control', 'required': 'true'}),
            'location': forms.TextInput(attrs={'class': 'form-control', 'required': 'true'}),
        }

class OpportunityForm(ModelForm):
    class Meta:
        model = Opportunity
        fields = ['lead', 'type', 'estimated_value', 'closing_date', 'status', 'description']
        widgets = {
            'type': forms.Select(attrs={'class': 'form-control'}),
            'status': forms.Select(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control'}),
            'lead': forms.Select(attrs={'class': 'form-control', 'required': 'true'}),
            'closing_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'estimated_value': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'required': 'true'}),
        }

class MaterialRequestForm(ModelForm):
    class Meta:
        model = MaterialRequest
        fields = ['lead', 'name', 'requirement', 'type', 'description','status']
        widgets = {
            'type': forms.Select(attrs={'class': 'form-control'}),
            'status': forms.Select(attrs={'class': 'form-control'}),
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'requirement': forms.Textarea(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control'}),
            'lead': forms.Select(attrs={'class': 'form-control', 'required': 'true'}),
        }

class ActivityLogForm(ModelForm):
    class Meta:
        model = ActivityLog
        fields = ['lead', 'user', 'type', 'description', 'follow_up_date']
        widgets = {
            'user': forms.Select(attrs={'class': 'form-control'}),
            'type': forms.Select(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control'}),
            'lead': forms.Select(attrs={'class': 'form-control', 'required': 'true'}),
            'follow_up_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        }

class WorkerCommissionsForm(ModelForm):
    class Meta:
        model = WorkerCommissions
        fields = ['lead', 'worker', 'opportunity', 'commission_amount', 'paid']
        widgets = {
            'lead': forms.Select(attrs={'class': 'form-control'}),
            'worker': forms.Select(attrs={'class': 'form-control'}),
            'opportunity': forms.Select(attrs={'class': 'form-control'}),
            'paid': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'commission_amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        }