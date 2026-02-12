from django.contrib import admin
from .models import (
    UserProfile, Project, ProjectActivity, ProjectNote, Worker,
    WorkerProject, ConstructionStage, LeadStatus, WorkerRole
)

# Register your models here.
admin.site.register(UserProfile)
admin.site.register(Project)
admin.site.register(ProjectActivity)
admin.site.register(ProjectNote)
admin.site.register(Worker)
admin.site.register(WorkerProject)
admin.site.register(ConstructionStage)
admin.site.register(LeadStatus)
admin.site.register(WorkerRole)