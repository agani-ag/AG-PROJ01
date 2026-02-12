from django.core.validators import RegexValidator
from django.conf import settings
import requests

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
    params = {'chat_id': GROUPS[chatID],'text': message,'parse_mode': 'Markdown'}
    response = requests.get(url, params=params)
    return response.json()