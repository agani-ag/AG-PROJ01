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
        cheque1 = requests.post(f'{BASEURL}/api/cheque_leaf_reminder', json={
            'user_ids': [5],
            'markdown': True
        })
        collection_days1 = requests.get(f'{BASEURL}/customers/api/collection-day/show?markdown=true&user_id=1')
        # Process responses
        data1 = resp1.json()
        data2 = resp2.json()
        cheque_data = cheque1.json()
        collection_data = collection_days1.json()
        # Telegram Push
        send_telegram_message(0, data1['markdown'])
        send_telegram_message(0, data2['markdown'])
        send_telegram_message(0, cheque_data['markdown'])
        send_telegram_message(0, collection_data['markdown'])