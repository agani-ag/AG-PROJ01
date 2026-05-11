from django.contrib import admin
from .models import (
    UserProfile, LinkRegistry,
    Holiday, Attendance, SalaryTransaction,
    JobRole, Worker, Leads, Opportunity,
    MaterialRequest, ActivityLog, WorkerCommissions,
    Reminder
)

# Register your models here.
admin.site.register(Leads)
admin.site.register(Worker)
admin.site.register(Holiday)
admin.site.register(JobRole)
admin.site.register(Reminder)
admin.site.register(Attendance)
admin.site.register(UserProfile)
admin.site.register(Opportunity)
admin.site.register(ActivityLog)
admin.site.register(LinkRegistry)
admin.site.register(MaterialRequest)
admin.site.register(SalaryTransaction)
admin.site.register(WorkerCommissions)