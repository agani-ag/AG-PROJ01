from django import forms
from datetime import date
from django.forms import ModelForm
from django.contrib.auth.models import User
from django.contrib.auth.forms import (
    AuthenticationForm
)
from .models import (
    ActivityLog, MaterialRequest, UserProfile,
    Worker, Leads, LinkRegistry, InstanceInfo,
    Opportunity, WorkerCommissions, PublicUser,
    Reminder, EmployeeIncentive
)
from urllib.parse import urlparse

class AuthForm(AuthenticationForm):
    class Meta:
        model = User
        fields = ('username', 'password')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'form-control'})
    
    def clean_username(self):
        # Automatically convert the username to lowercase
        username = self.cleaned_data.get('username')
        if username:
            return username.lower()
        return username

class SignupForm(ModelForm):
    class Meta:
        model = User
        fields = ('username',)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.update({'class': 'form-control'})

    def clean_username(self):
        # Automatically convert the username to lowercase
        username = self.cleaned_data.get('username')
        if username:
            return username.lower()
        return username

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
            'pincode': forms.TextInput(attrs={'class': 'form-control'}),
            'address': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'dob': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'salary': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'phone': forms.TextInput(attrs={'class': 'form-control', 'required': 'true'}),
        }

class UserProfileEditForm(ModelForm):
    class Meta:
        model = UserProfile
        fields = ['name', 'dob', 'email', 'phone', 'address', 'pincode', 'latitude', 'longitude', 'salary', 'working_days', 'special_menus_access']
        widgets = {
            'working_days': forms.CheckboxSelectMultiple(),
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'pincode': forms.TextInput(attrs={'class': 'form-control'}),
            'latitude': forms.NumberInput(attrs={'class': 'form-control'}),
            'longitude': forms.NumberInput(attrs={'class': 'form-control'}),
            'address': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'dob': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'salary': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'phone': forms.TextInput(attrs={'class': 'form-control', 'required': 'true'}),
            'special_menus_access': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

class UserSelfProfileForm(ModelForm):
    """Profile fields a user may edit for THEMSELVES. Excludes salary,
    working_days and special_menus_access so a self-save never wipes them
    (those are admin-only and not rendered on the self page)."""
    class Meta:
        model = UserProfile
        fields = ['name', 'dob', 'email', 'phone', 'address', 'pincode', 'latitude', 'longitude']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'pincode': forms.TextInput(attrs={'class': 'form-control'}),
            'latitude': forms.NumberInput(attrs={'class': 'form-control'}),
            'longitude': forms.NumberInput(attrs={'class': 'form-control'}),
            'address': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'dob': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control', 'required': 'true'}),
        }


class PublicUserForm(ModelForm):
    class Meta:
        model = PublicUser
        fields = ['name', 'email', 'phone', 'address', 'pincode', 
                  'business_name', 'username', 'password', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'pincode': forms.NumberInput(attrs={'class': 'form-control'}),
            'business_name': forms.TextInput(attrs={'class': 'form-control'}),
            'address': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'username': forms.TextInput(attrs={'class': 'form-control', 'required': 'true'}),
            'password': forms.TextInput(attrs={'class': 'form-control', 'required': 'true'}),
        }

class PublicUserSelfForm(ModelForm):
    """Fields a public (non-logged-in) user may edit for themselves. Excludes
    username, password and is_active so self-editing can't change credentials
    or reactivate a disabled account."""
    class Meta:
        model = PublicUser
        fields = ['name', 'email', 'phone', 'address', 'pincode', 'business_name']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'pincode': forms.NumberInput(attrs={'class': 'form-control'}),
            'business_name': forms.TextInput(attrs={'class': 'form-control'}),
            'address': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }


class LinkRegistryForm(ModelForm):
    class Meta:
        model = LinkRegistry
        fields = ['user', 'name', 'url', 'is_active']
        widgets = {
            'user': forms.Select(attrs={'class': 'form-control'}),
            'url': forms.Textarea(attrs={'class': 'form-control'}),
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def clean_url(self):
        url = self.cleaned_data.get('url')

        if not url:
            return url

        # If scheme is missing, add https://
        parsed = urlparse(url)
        if not parsed.scheme:
            url = 'https://' + url

        return url

class InstanceInfoForm(ModelForm):
    class Meta:
        model = InstanceInfo
        fields = ['name', 'base_url', 'endpoint', 'description', 
                  'auth_key', 'auth_value', 'is_active', 'login_notified']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'base_url': forms.URLInput(attrs={'class': 'form-control'}),
            'endpoint': forms.TextInput(attrs={'class': 'form-control'}),
            'auth_key': forms.TextInput(attrs={'class': 'form-control'}),
            'auth_value': forms.TextInput(attrs={'class': 'form-control'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'login_notified': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def clean_base_url(self):
        base_url = self.cleaned_data.get('base_url')
        if not base_url:
            return base_url
        # If scheme is missing, add https://
        parsed = urlparse(base_url)
        if not parsed.scheme:
            base_url = 'https://' + base_url
        return base_url

    def clean_name(self):
        name = self.cleaned_data.get('name')
        if not name:
            return name
        normalized_name = name.strip().upper()
        # Block reserved name
        if normalized_name == 'S1':
            raise forms.ValidationError("The name 'S1' is not accepted.")
        # Exclude current instance (THIS IS THE FIX)
        qs = InstanceInfo.objects.filter(name=normalized_name)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("Instance with this name already exists.")
        return normalized_name

class WorkerForm(ModelForm):
    leads = forms.ModelMultipleChoiceField(
        queryset=Leads.objects.all(),
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-control'})
    )
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
        fields = ['name', 'email', 'phone', 'location', 'pincode', 'latitude', 'longitude', 'workers',
                  'type', 'project_stage', 'source_type', 'status', 'assigned_to', 'referral_worker']
        widgets = {
            'type': forms.Select(attrs={'class': 'form-control'}),
            'status': forms.Select(attrs={'class': 'form-control'}),
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'pincode': forms.TextInput(attrs={'class': 'form-control'}),
            'source_type': forms.Select(attrs={'class': 'form-control'}),
            'assigned_to': forms.Select(attrs={'class': 'form-control'}),
            'latitude': forms.NumberInput(attrs={'class': 'form-control'}),
            'project_stage': forms.Select(attrs={'class': 'form-control'}),
            'longitude': forms.NumberInput(attrs={'class': 'form-control'}),
            'referral_worker': forms.Select(attrs={'class': 'form-control'}),
            'workers': forms.SelectMultiple(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control', 'required': 'true'}),
            'location': forms.TextInput(attrs={'class': 'form-control', 'required': 'true'}),
        }

    def clean(self):
        # Enforce server-side what the widgets require client-side.
        cleaned = super().clean()
        for field in ('phone', 'location'):
            if not cleaned.get(field):
                self.add_error(field, 'This field is required.')
        return cleaned

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
        fields = ['lead', 'name', 'type', 'description','status']
        widgets = {
            'type': forms.Select(attrs={'class': 'form-control'}),
            'status': forms.Select(attrs={'class': 'form-control'}),
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control'}),
            'lead': forms.Select(attrs={'class': 'form-control', 'required': 'true'}),
        }

WEEKDAY_CHOICES = [
    (None, '---'),
    (1, 'Sunday'),
    (2, 'Monday'),
    (3, 'Tuesday'),
    (4, 'Wednesday'),
    (5, 'Thursday'),
    (6, 'Friday'),
    (7, 'Saturday'),
]

class ReminderForm(ModelForm):
    class Meta:
        model = Reminder
        fields = ['device', 'title', 'body', 'type', 'hour', 'minute',
                  'weekday', 'seconds', 'date', 'enabled', 'sound']
        widgets = {
            'device': forms.Select(attrs={'class': 'form-control'}),
            'title': forms.TextInput(attrs={'class': 'form-control'}),
            'body': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'type': forms.Select(attrs={'class': 'form-control'}),
            'hour': forms.NumberInput(attrs={'class': 'form-control', 'min': 0, 'max': 23}),
            'minute': forms.NumberInput(attrs={'class': 'form-control', 'min': 0, 'max': 59}),
            'weekday': forms.Select(choices=WEEKDAY_CHOICES, attrs={'class': 'form-control'}),
            'seconds': forms.NumberInput(attrs={'class': 'form-control', 'min': 60}),
            'date': forms.DateTimeInput(attrs={'class': 'form-control', 'type': 'datetime-local'}),
            'enabled': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'sound': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
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

class EmployeeIncentiveForm(ModelForm):
    class Meta:
        model = EmployeeIncentive
        fields = ['amount', 'is_paid', 'date', 'user', 'description']
        widgets = {
            'user': forms.Select(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control'}),
            'is_paid': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        }