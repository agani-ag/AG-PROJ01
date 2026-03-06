# Django imports
from calendar import Calendar
from datetime import date, datetime
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import (
    render, redirect, get_object_or_404
)
# Imports
from ..models import (
    UserProfile, Attendance, Holiday
)
from ..utils import (
    generate_attendance, calculate_salary
)
# =============== Attendance Views ===============
def attendance_calendar(request, user_id, month=None, year=None):
    user = get_object_or_404(UserProfile, id=user_id)
    today = date.today()
    month = int(month or today.month)
    year = int(year or today.year)

    # Generate attendance at the beginning of the month
    generate_attendance(user, year, month)

    cal = Calendar()
    month_days = cal.monthdatescalendar(year, month)
    holidays = Holiday.objects.filter(date__year=year, date__month=month)
    holiday_dates = set(h.date for h in holidays)

    month_weeks = []
    for week in month_days:
        week_days = []
        for day in week:
            if day.month == month:
                attendance = Attendance.objects.filter(user=user, date=day).first()
                is_holiday = day in holiday_dates
                week_days.append({'day': day, 'attendance': attendance, 'is_holiday': is_holiday})
            else:
                week_days.append(None)
        month_weeks.append(week_days)

    # Calculate total working days, present, absent, and salary
    total_working_days = Attendance.objects.filter(user=user, date__year=year, date__month=month).count()
    total_present = Attendance.objects.filter(user=user, date__year=year, date__month=month, present=True).count()
    total_absent = total_working_days - total_present
    salary = calculate_salary(user, year, month)  # Dynamically calculate salary based on updated attendance

    # Pagination (previous/next months)
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
        'total_working_days': total_working_days,
        'total_present': total_present,
        'total_absent': total_absent,
        'salary': salary
    })

def ajax_mark_attendance(request):
    """
    Toggle attendance via AJAX.
    Expects POST: user_id, date (YYYY-MM-DD)
    """
    if request.method == "POST" and request.headers.get('x-requested-with') == 'XMLHttpRequest':
        user_id = request.POST.get('user_id')
        date_str = request.POST.get('date')
        try:
            # Fetch the user and attendance date
            user = UserProfile.objects.get(id=user_id)
            attendance_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            
            # Toggle the attendance (create if not exists)
            attendance, created = Attendance.objects.get_or_create(
                user=user, date=attendance_date,
                defaults={'present': True}
            )
            if not created:
                attendance.present = not attendance.present
                attendance.save()

            # Recalculate totals and salary
            total_working_days = Attendance.objects.filter(user=user, date__year=attendance_date.year, date__month=attendance_date.month).count()
            total_present = Attendance.objects.filter(user=user, date__year=attendance_date.year, date__month=attendance_date.month, present=True).count()
            total_absent = total_working_days - total_present

            # Dynamically calculate salary after attendance toggle
            salary = calculate_salary(user, attendance_date.year, attendance_date.month)  # Ensure salary is recalculated

            return JsonResponse({
                'status': 'success',
                'present': attendance.present,
                'total_working_days': total_working_days,
                'total_present': total_present,
                'total_absent': total_absent,
                'salary': salary  # Return updated salary in the response
            })
        except Exception as e:
            return JsonResponse({'status': 'error', 'error': str(e)})

    return JsonResponse({'status': 'error', 'error': 'Invalid request'})