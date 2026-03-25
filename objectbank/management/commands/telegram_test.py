from django.core.management.base import BaseCommand
from ...utils import send_telegram_message
from main import settings
import requests

BASEURL = settings.PROJ02_URL

class Command(BaseCommand):
    help = "Sends a test message via the Telegram bot"

    def handle(self, *args, **kwargs):
        resp1 = requests.post(f'{BASEURL}/api/reports/overdue', json={
            'user_ids': [1],
            'days': 90,
            'markdown': True
        })
        resp2 = requests.post(f'{BASEURL}/api/reports/overdue', json={
            'user_ids': [1],
            'days': 120,
            'markdown': True
        })
        data1 = resp1.json()
        data2 = resp2.json()
        send_telegram_message(0, data1['markdown'])
        send_telegram_message(0, data2['markdown'])