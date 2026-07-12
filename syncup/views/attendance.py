# Django imports
from calendar import Calendar
from datetime import date, datetime
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import (
    render, redirect, get_object_or_404
)
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
# Imports
from ..models import (
    User, UserProfile,
    Attendance, Holiday, SalaryTransaction
)
from ..utils import (
    generate_attendance, calculate_salary
)
# =============== Attendance Views ===============
@login_required
def attendances(request):
    if not request.user.is_superuser:
        messages.error(request, "You are not authorized to view all attendances.")
        return redirect('profile_edit')
    context = {}
    users = User.objects.filter(is_active=True, is_staff=True).exclude(is_superuser=True)
    user_profiles = UserProfile.objects.filter(user__in=users).select_related('user')
    context["user_profiles"] = user_profiles
    return render(request, 'attendance/attendances.html', context)

@login_required
def attendance_calendar(request, user_id):
    user = get_object_or_404(UserProfile, id=user_id)
    # A non-superuser may only view their own calendar (salary is shown here).
    if not (request.user.is_superuser or user.user_id == request.user.id):
        messages.error(request, "You can only view your own attendance.")
        return redirect('profile_edit')
    today = date.today()

    # Get month/year from query params, defaulting to today; validate to avoid 500s.
    try:
        month = int(request.GET.get('month', today.month))
        year = int(request.GET.get('year', today.year))
    except (TypeError, ValueError):
        month, year = today.month, today.year
    if not 1 <= month <= 12:
        month = today.month
    if not 1900 <= year <= 2100:
        year = today.year

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
        
        # Ensure each week has exactly 7 days (pad with None if needed)
        while len(week_days) < 7:
            week_days.append(None)
        
        month_weeks.append(week_days)

    # Calculate total working days, present, absent, and salary
    total_working_days = Attendance.objects.filter(user=user, date__year=year, date__month=month).count()
    total_present = Attendance.objects.filter(user=user, date__year=year, date__month=month, present=True).count()
    total_absent = total_working_days - total_present
    salary = calculate_salary(user, year, month)  # Dynamically calculate salary based on updated attendance

    # Get credits and bonus for this month
    salary_transaction = SalaryTransaction.objects.filter(user=user, month=month, year=year).first()
    credits = salary_transaction.credits if salary_transaction else 0.0
    bonus = salary_transaction.bonus if salary_transaction else 0.0

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
        'salary': salary,
        'credits': credits,
        'bonus': bonus
    })

@login_required
@require_POST
def ajax_mark_attendance(request):
    """
    Toggle attendance via AJAX.
    Expects POST: user_id, date (YYYY-MM-DD)
    """
    if not request.user.is_superuser:
        return JsonResponse({'status': 'error', 'error': 'Not authorized'}, status=403)
    if request.method == "POST":
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
                'salary': float(salary)  # Ensure salary is a JSON-serializable float
            })
        except Exception as e:
            return JsonResponse({'status': 'error', 'error': str(e)})

    return JsonResponse({'status': 'error', 'error': 'Invalid request'})

@login_required
@require_POST
def ajax_update_credit_bonus(request):
    """
    Update credits or bonus for a user's month via AJAX.
    Expects POST: user_id, month, year, field_type ('credits' or 'bonus'), amount
    """
    if not request.user.is_superuser:
        return JsonResponse({'status': 'error', 'error': 'Not authorized'}, status=403)
    if request.method == "POST":
        user_id = request.POST.get('user_id')
        month = request.POST.get('month')
        year = request.POST.get('year')
        field_type = request.POST.get('field_type')  # 'credits' or 'bonus'
        amount = request.POST.get('amount')
        
        try:
            user = UserProfile.objects.get(id=user_id)
            month = int(month)
            year = int(year)
            amount = float(amount)
            
            if field_type not in ['credits', 'bonus']:
                return JsonResponse({'status': 'error', 'error': 'Invalid field type'})
            
            # Get or create the salary transaction
            salary_transaction, created = SalaryTransaction.objects.get_or_create(
                user=user,
                month=month,
                year=year,
                defaults={
                    'base_salary': user.salary or 0,
                    'credits': 0,
                    'bonus': 0,
                    'calculated_salary': 0
                }
            )
            
            # Update the appropriate field
            if field_type == 'credits':
                salary_transaction.credits = amount
            else:
                salary_transaction.bonus = amount
            
            salary_transaction.save()
            
            # Recalculate salary
            from ..utils import calculate_salary
            calculate_salary(user, year, month)
            
            # Get updated transaction
            salary_transaction.refresh_from_db()
            
            return JsonResponse({
                'status': 'success',
                'credits': float(salary_transaction.credits),
                'bonus': float(salary_transaction.bonus),
                'salary': float(salary_transaction.calculated_salary)
            })
        except Exception as e:
            return JsonResponse({'status': 'error', 'error': str(e)})
    
    return JsonResponse({'status': 'error', 'error': 'Invalid request'})