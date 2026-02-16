from django.core.management.base import BaseCommand
from ...utils import send_telegram_message

class Command(BaseCommand):
    help = "Sends a test message via the Telegram bot"

    def handle(self, *args, **kwargs):
        send_telegram_message(0, "This is a test message from the Telegram bot.")
        send_telegram_message(1, "This is a test message from the Telegram bot.")