from django.core.management.base import BaseCommand
from ...utils import send_telegram_message
from main import settings
import requests

BASEURL = settings.PROJ02_URL

class Command(BaseCommand):
    help = "Sends a test message via the Telegram bot"

    def handle(self, *args, **kwargs):
        resp = requests.post(f'{BASEURL}/api/reports/overdue', json={
            'user_ids': [1],
            'days': 90,
            'markdown': True
        })
        data = resp.json()
        send_telegram_message(0, "This is a test message from the Telegram bot.")
        send_telegram_message(1, data['markdown'])