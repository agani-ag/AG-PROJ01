from django.core.validators import RegexValidator
from django.conf import settings
from calendar import monthrange
from decimal import Decimal
from datetime import date
import requests
import base64
import json

# =============== Validators ===============
phone_validator = RegexValidator(
    regex=r'^\+?\d{7,15}$',
    message="Phone number must be entered in the format: '+999999999'. Up to 15 digits allowed."
)

pincode_validator = RegexValidator(
    regex=r'^\d{4,10}$',
    message="Pincode must be between 4 and 10 digits."
)

# =============== Telegram Bot ===============
GROUPS = settings.TELEGRAM_GROUPS
BOT = settings.TELEGRAM_BOT_TOKEN

def send_telegram_message(chatID: int, message):
    url = f'https://api.telegram.org/bot{BOT}/sendMessage'
    params = {'chat_id': GROUPS[chatID],'text': message,'parse_mode': 'MarkdownV2'}
    response = requests.get(url, params=params)
    return response.json()

# =============== Attendance Generation ===============
def generate_attendance(user, year, month):
    from .models import Holiday, Attendance
    _, days_in_month = monthrange(year, month)
    holidays = set(Holiday.objects.filter(date__year=year, date__month=month).values_list('date', flat=True))
    working_days_map = {'MON':0,'TUE':1,'WED':2,'THU':3,'FRI':4,'SAT':5,'SUN':6}
    user_working_days = [working_days_map[d] for d in user.working_days]

    for day in range(1, days_in_month + 1):
        dt = date(year, month, day)
        if dt.weekday() in user_working_days and dt not in holidays:
            Attendance.objects.get_or_create(user=user, date=dt, defaults={'present': False})

def calculate_salary(user, year, month):
    from .models import Attendance, SalaryTransaction
    attendances = Attendance.objects.filter(user=user, date__year=year, date__month=month, present=True)
    total_working_days = Attendance.objects.filter(user=user, date__year=year, date__month=month).count()
    
    base_salary = user.salary or 0
    daily_rate = base_salary / total_working_days if total_working_days else 0
    salary = daily_rate * attendances.count()
    salary = Decimal(salary)

    # Fetch credits and bonus for this month
    transaction = SalaryTransaction.objects.filter(user=user, month=month, year=year).first()
    credits = Decimal(transaction.credits) if transaction else Decimal(0)
    bonus = Decimal(transaction.bonus) if transaction else Decimal(0)
    final_salary = salary - credits + bonus

    SalaryTransaction.objects.update_or_create(
        user=user,
        month=month,
        year=year,
        defaults={
            'base_salary': base_salary,
            'credits': credits,
            'bonus': bonus,
            'calculated_salary': final_salary
        }
    )
    return final_salary

# =============== JSON Encoding/Decoding ===============
def encode_via_json(data):
    json_str = json.dumps(data)
    json_bytes = json_str.encode("utf-8")
    encoded = base64.urlsafe_b64encode(json_bytes).decode("utf-8")
    return encoded

def decode_via_json(encoded):
    json_bytes = base64.urlsafe_b64decode(encoded)
    json_str = json_bytes.decode("utf-8")
    data = json.loads(json_str)
    return data