from google.auth.transport.requests import Request
from django.core.validators import RegexValidator
from requests.adapters import HTTPAdapter
from google.oauth2 import service_account
from urllib3.util.retry import Retry
from django.conf import settings
from calendar import monthrange
from typing import Optional
from decimal import Decimal
from datetime import date
import threading
import requests
import logging
import base64
import json
import time
import re

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
logger = logging.getLogger(__name__)

_MARKDOWN_V2_SPECIAL = re.compile(r'([_*\[\]()~`>#+\-=|{}.!\\])')

def escape_markdown_v2(text: str) -> str:
    """Escape all MarkdownV2 special characters in plain text."""
    return _MARKDOWN_V2_SPECIAL.sub(r'\\\1', text)

def send_telegram_message(chatID: int, message):
    url = f'https://api.telegram.org/bot{BOT}/sendMessage'
    payload = {
        'chat_id': GROUPS[chatID],
        'text': message,
        'parse_mode': 'MarkdownV2'
    }
    headers = {'Content-Type': 'application/json'}

    # 1. Configure a Retry Strategy
    # This will retry on 502, 503, or 504 errors specifically
    retry_strategy = Retry(
        total=3,                # Total number of retries
        backoff_factor=1,       # Wait 1s, 2s, 4s between retries
        status_forcelist=[502, 503, 504], 
        allowed_methods=["POST"] # Ensure it retries on POST requests
    )
    
    adapter = HTTPAdapter(max_retries=retry_strategy)
    
    try:
        # 2. Use a Session to apply the retry logic
        with requests.Session() as session:
            session.mount("https://", adapter)
            
            # Lowered timeout to 3.5s so retries don't take forever
            response = session.post(
                url, 
                json=payload, 
                headers=headers, 
                timeout=3.5 
            )
            
            data = response.json()

            # Fallback: if MarkdownV2 parsing fails (400), retry as plain text
            if response.status_code == 400:
                description = data.get('description', '')
                logger.warning(f"MarkdownV2 rejected (400): {description}. Retrying as plain text.")
                payload.pop('parse_mode')
                response = session.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=3.5
                )
                data = response.json()

            response.raise_for_status()
            
            if not data.get('ok'):
                logger.warning(f"Telegram API returned error: {data.get('description')}")
                return None
                
            return data

    except requests.exceptions.RetryError:
        logger.error("Telegram Proxy failed after multiple retry attempts (503).")
    except requests.exceptions.Timeout:
        logger.error("Telegram notification timed out.")
    except requests.exceptions.ProxyError:
        logger.error("Initial Proxy connection failed (503).")
    except requests.exceptions.RequestException as e:
        logger.error(f"Telegram communication error: {e}")
    
    return None

# =============== Attendance Generation ===============
def generate_attendance(user, year, month):
    from .models import Holiday, Attendance
    _, days_in_month = monthrange(year, month)
    holidays = set(Holiday.objects.filter(date__year=year, date__month=month).values_list('date', flat=True))
    working_days_map = {'MON':0,'TUE':1,'WED':2,'THU':3,'FRI':4,'SAT':5,'SUN':6}
    # working_days can be None/empty (nullable field) — guard against iterating None.
    user_working_days = {working_days_map[d] for d in (user.working_days or []) if d in working_days_map}

    # No configured working days → don't create or delete anything (safest).
    if not user_working_days:
        return

    existing = dict(
        Attendance.objects.filter(user=user, date__year=year, date__month=month)
        .values_list('date', 'present')
    )

    # Create the missing working-day rows in one query.
    to_create = []
    for day in range(1, days_in_month + 1):
        dt = date(year, month, day)
        if dt.weekday() in user_working_days and dt not in holidays and dt not in existing:
            to_create.append(Attendance(user=user, date=dt, present=False))
    if to_create:
        Attendance.objects.bulk_create(to_create, ignore_conflicts=True)

    # Reconcile: drop auto-generated (absent) rows that no longer fall on a
    # working day or now land on a holiday. Never delete days marked present.
    stale = [
        dt for dt, present in existing.items()
        if not present and (dt.weekday() not in user_working_days or dt in holidays)
    ]
    if stale:
        Attendance.objects.filter(user=user, date__in=stale, present=False).delete()

def calculate_salary(user, year, month):
    from .models import Attendance, SalaryTransaction
    present_count = Attendance.objects.filter(user=user, date__year=year, date__month=month, present=True).count()
    total_working_days = Attendance.objects.filter(user=user, date__year=year, date__month=month).count()

    base_salary = user.salary if user.salary is not None else Decimal(0)
    if not isinstance(base_salary, Decimal):
        base_salary = Decimal(str(base_salary))
    daily_rate = (base_salary / total_working_days) if total_working_days else Decimal(0)
    salary = daily_rate * present_count

    # Fetch credits and bonus for this month
    transaction = SalaryTransaction.objects.filter(user=user, month=month, year=year).first()
    credits = Decimal(transaction.credits) if transaction else Decimal(0)
    bonus = Decimal(transaction.bonus) if transaction else Decimal(0)
    final_salary = (salary - credits + bonus).quantize(Decimal('0.01'))

    # Persist only when something actually changed — avoids a DB write on every
    # calendar GET while keeping the stored value fresh for other readers.
    if (transaction is None
            or transaction.calculated_salary != final_salary
            or transaction.base_salary != base_salary):
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

# =============== FCM Constants ===============
FIREBASE_PROJECT_ID = settings.FIREBASE_PROJECT_ID
SERVICE_ACCOUNT_FILE = settings.SERVICE_ACCOUNT_FILE
FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"

# =============== FCM Token Management ===============
class FCMAuthError(Exception):
    """Custom exception for FCM auth failures."""
    pass

class FCMTokenManager:
    """
    Production-grade FCM access token manager with:
    - TTL-based caching
    - Thread safety
    - Early refresh buffer
    """

    def __init__(self, service_account_file: str, refresh_buffer: int = 300):
        """
        Args:
            service_account_file (str): Path to service account JSON file
            refresh_buffer (int): Seconds before expiry to refresh token (default: 5 min)
        """
        self.service_account_file = service_account_file
        self.refresh_buffer = refresh_buffer

        self._token: Optional[str] = None
        self._expiry: float = 0
        self._lock = threading.Lock()

    def get_token(self) -> str:
        """
        Returns a valid FCM access token.
        Refreshes only if expired or near expiry.
        """
        now = time.time()

        # Fast path (no lock)
        if self._token and now < self._expiry - self.refresh_buffer:
            return self._token

        # Slow path (with lock)
        with self._lock:
            # Double-check after acquiring lock
            now = time.time()
            if self._token and now < self._expiry - self.refresh_buffer:
                return self._token

            self._refresh_token()
            return self._token

    def _refresh_token(self):
        """Refresh the access token from Google."""
        try:
            credentials = service_account.Credentials.from_service_account_file(
                self.service_account_file,
                scopes=[FCM_SCOPE],
            )

            request = Request()
            credentials.refresh(request)

            if not credentials.token:
                raise FCMAuthError("Token refresh succeeded but token is empty.")

            if not credentials.expiry:
                raise FCMAuthError("Token expiry missing from credentials.")

            self._token = credentials.token
            self._expiry = credentials.expiry.timestamp()

        except FileNotFoundError as e:
            raise FCMAuthError(
                f"Service account file not found: {self.service_account_file}"
            ) from e
        except Exception as e:
            raise FCMAuthError(f"Failed to generate FCM access token: {e}") from e

# Singleton instance (recommended for apps)
_fcm_token_manager: Optional[FCMTokenManager] = None

def init_fcm(service_account_file: str):
    global _fcm_token_manager
    _fcm_token_manager = FCMTokenManager(service_account_file)


def get_fcm_token() -> str:
    """Public function to get token (used across app)."""
    if not _fcm_token_manager:
        raise FCMAuthError("FCMTokenManager not initialized. Call init_fcm() first.")
    return _fcm_token_manager.get_token()