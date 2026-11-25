"""The cleanup cron removes old Telegram send-log entries on the push log's schedule (AppConfig.cleanup_log_days).
Run: python manage.py test mobileapi.tests_cleanup_telegram_logs"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .cleanup import run_cleanup
from .models import AppConfig, AppNotificationLog, AppPartner, AppTelegramLog


def telegram_log(days_old, **fields):
    log = AppTelegramLog.objects.create(chat_id="-100555", text=f"{days_old} days old", **fields)
    AppTelegramLog.objects.filter(pk=log.pk).update(created_at=timezone.now() - timedelta(days=days_old))
    return log


def push_log(days_old):
    log = AppNotificationLog.objects.create(title="t", body=f"{days_old} days old")
    AppNotificationLog.objects.filter(pk=log.pk).update(sent_at=timezone.now() - timedelta(days=days_old))
    return log


def set_days(days):
    cfg = AppConfig.load()
    cfg.cleanup_log_days = days
    cfg.save()


class TelegramLogCleanupTests(TestCase):
    def test_old_entries_go_and_recent_ones_stay(self):
        set_days(30)
        partner, _ = AppPartner.issue("GSTSync")
        telegram_log(31)
        telegram_log(45, partner=partner, status="failed", error="chat not found")
        keep = [telegram_log(29), telegram_log(0)]
        stats = run_cleanup()
        self.assertEqual(stats["telegram_logs"], 2)
        self.assertEqual(sorted(AppTelegramLog.objects.values_list("pk", flat=True)), sorted(k.pk for k in keep))

    def test_the_same_days_as_the_push_log(self):
        set_days(7)
        telegram_log(8), push_log(8)
        telegram_log(6), push_log(6)
        stats = run_cleanup()
        self.assertEqual((stats["telegram_logs"], stats["notification_logs"]), (1, 1))
        self.assertEqual((AppTelegramLog.objects.count(), AppNotificationLog.objects.count()), (1, 1))

    def test_zero_keeps_everything(self):
        set_days(0)
        telegram_log(400)
        self.assertNotIn("telegram_logs", run_cleanup())
        self.assertEqual(AppTelegramLog.objects.count(), 1)

    @override_settings(CRON_KEY="k")
    def test_every_cleanup_cron_run_does_it_and_says_so(self):
        set_days(30)
        telegram_log(60)
        reply = self.client.get("/cron/cleanup", HTTP_X_CRON_KEY="k").json()
        self.assertEqual(reply["telegram_logs"], 1)
        self.assertFalse(AppTelegramLog.objects.exists())

    def test_the_setting_says_it_covers_both(self):
        self.client.force_login(get_user_model().objects.create_superuser("boss", password="pw"))
        page = self.client.get(reverse("mobile_config")).content.decode()
        self.assertIn("Push and Telegram logs", page)
