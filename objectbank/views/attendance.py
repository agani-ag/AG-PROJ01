# Django imports
from calendar import Calendar
from datetime import date, datetime
from django.contrib import messages
from django.shortcuts import (
    render, redirect, get_object_or_404
)
# Imports
from ..models import UserProfile, Attendance
from ..utils import generate_attendance

# =============== Attendance Views ===============
def attendance_calendar(request, user_id, month=None, year=None):
    user = get_object_or_404(UserProfile, id=user_id)
    today = date.today()
    month = int(month or today.month)
    year = int(year or today.year)

    # Generate attendance for all working days if missing
    generate_attendance(user, year, month)

    cal = Calendar()
    month_days = cal.monthdatescalendar(year, month)
    month_weeks = []
    for week in month_days:
        week_days = []
        for day in week:
            if day.month == month:
                attendance = Attendance.objects.filter(user=user, date=day).first()
                week_days.append({'day': day, 'attendance': attendance})
            else:
                week_days.append(None)
        month_weeks.append(week_days)

    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1

    return render(request, 'attendance/calendar.html', {
        'profile': user,
        'month_weeks': month_weeks,
        'month': month,
        'year': year,
        'prev_month': prev_month,
        'prev_year': prev_year,
        'next_month': next_month,
        'next_year': next_year,
    })

def mark_attendance(request, user_id, date_str):
    user = get_object_or_404(UserProfile, id=user_id)
    attendance_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    attendance, created = Attendance.objects.get_or_create(
        user=user, date=attendance_date,
        defaults={'present': True}
    )
    if not created:
        attendance.present = not attendance.present
        attendance.save()
    messages.success(request, f"{attendance_date} marked as {'Present' if attendance.present else 'Absent'}")
    return redirect('attendance_calendar', user_id=user.id)