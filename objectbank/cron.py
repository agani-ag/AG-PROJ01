from utils import send_telegram_message

def test_telegram():
    send_telegram_message(0,"Test message from cron job...")