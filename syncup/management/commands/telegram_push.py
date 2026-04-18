from django.core.management.base import BaseCommand
from ...utils import send_telegram_message
from main import settings
import requests

BASEURL = settings.PROJ02_URL

class Command(BaseCommand):
    help = "Sends a test message via the Telegram bot"

    def handle(self, *args, **kwargs):
        try:
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
        except Exception as e:
            self.stderr.write(f'Error fetching report data: {e}')
            return
        # Telegram Push
        messages = [
            ('overdue_90', data1.get('markdown')),
            ('overdue_120', data2.get('markdown')),
            ('cheque_reminder', cheque_data.get('markdown')),
            ('collection_days', collection_data.get('markdown')),
        ]
        for label, msg in messages:
            if not msg:
                self.stderr.write(f'[{label}] No markdown content, skipping.')
                continue
            try:
                send_telegram_message(0, msg)
            except Exception as e:
                self.stderr.write(f'[{label}] Failed to send Telegram message: {e}')