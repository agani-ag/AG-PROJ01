from django.core.management.base import BaseCommand
from mobileapi import telegram as tg
from mobileapi.models import AppConfig, AppTelegramLog
from main import settings
import requests

BASEURL = settings.PROJ02_URL

class Command(BaseCommand):
    help = "Pulls the PROJ02 reports and sends them to our Telegram chat (Telegram page → bot + chat id)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--chat", dest="chat_id", default="",
            help="Chat id to send to (default: the report chat id saved in App config).",
        )

    def handle(self, *args, **kwargs):
        chat_id = (kwargs.get("chat_id") or AppConfig.load().telegram_admin_chat_id or "").strip()
        if not tg.is_configured():
            self.stderr.write("Telegram bot is not configured — set the bot token on /mobile/telegram.")
            return
        if not chat_id:
            self.stderr.write("No report chat id — set one on /mobile/telegram, or pass --chat <id>.")
            return
        messages = []
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
            messages.append(('overdue_90', data1.get('markdown')))
            messages.append(('overdue_120', data2.get('markdown')))
            cheque_data = cheque1.json()
            if cheque_data.get('count') != 0:
                messages.append(('cheque_leaf_reminder', cheque_data.get('markdown')))
            collection_data = collection_days1.json()
            if collection_data.get('count') != 0:
                messages.append(('collection_days', collection_data.get('markdown')))
        except Exception as e:
            self.stderr.write(f'Error fetching report data: {e}')
            return
        # Telegram Push
        for label, msg in messages:
            if not msg:
                self.stderr.write(f'[{label}] No markdown content, skipping.')
                continue
            ok, info = tg.send(chat_id, msg, parse_mode="MarkdownV2")
            AppTelegramLog.objects.create(
                partner=None, chat_id=chat_id[:64], text=msg,
                status="sent" if ok else "failed",
                message_id=info if ok else "", error="" if ok else info,
            )
            if ok:
                self.stdout.write(f'[{label}] sent')
            else:
                self.stderr.write(f'[{label}] Failed to send Telegram message: {info}')