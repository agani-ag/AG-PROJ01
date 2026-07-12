from django.contrib import admin
from .models import (
    UserProfile, LinkRegistry,
    Holiday, Attendance, SalaryTransaction,
    JobRole, Worker, Leads, Opportunity,
    MaterialRequest, ActivityLog, WorkerCommissions,
    Reminder
)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('name', 'user', 'salary', 'working_days')
    search_fields = ('name', 'user__username', 'email')
    actions = ['set_working_days_mon_sat', 'set_working_days_mon_fri']

    @admin.action(description="Set working days → Mon–Sat")
    def set_working_days_mon_sat(self, request, queryset):
        updated = 0
        for profile in queryset:
            profile.working_days = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT']
            profile.save(update_fields=['working_days'])
            updated += 1
        self.message_user(request, f"Set Mon–Sat working days for {updated} profile(s).")

    @admin.action(description="Set working days → Mon–Fri")
    def set_working_days_mon_fri(self, request, queryset):
        updated = 0
        for profile in queryset:
            profile.working_days = ['MON', 'TUE', 'WED', 'THU', 'FRI']
            profile.save(update_fields=['working_days'])
            updated += 1
        self.message_user(request, f"Set Mon–Fri working days for {updated} profile(s).")


# Register your models here.
admin.site.register(Leads)
admin.site.register(Worker)
admin.site.register(Holiday)
admin.site.register(JobRole)
admin.site.register(Reminder)
admin.site.register(Attendance)
admin.site.register(Opportunity)
admin.site.register(ActivityLog)
admin.site.register(LinkRegistry)
admin.site.register(MaterialRequest)
admin.site.register(SalaryTransaction)
admin.site.register(WorkerCommissions)