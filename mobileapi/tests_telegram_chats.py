"""The Telegram chat list ("Chats using this bot"), which outlives the removed inbox, and the menu.
No calls reach Telegram: every Bot API function is patched.
Run: python manage.py test mobileapi.tests_telegram_chats"""
from datetime import datetime, timezone as dt_timezone
from unittest import mock

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase

from . import cron_views, telegram_chats
from .models import AppConfig, TelegramChat

GROUP = {"id": -100123, "type": "supergroup", "title": "Ops"}
SENT = 1700000000


def message(update_id=7, chat=GROUP):
    return {"update_id": update_id, "message": {"message_id": 1, "date": SENT, "chat": chat,
                                                "from": {"first_name": "Asha"}, "text": "hi"}}


class IngestTests(TestCase):
    def test_a_message_registers_its_chat(self):
        result = telegram_chats.ingest(message())
        self.assertTrue(result["created"])
        row = TelegramChat.objects.get(chat_id="-100123")
        self.assertEqual((row.title, row.chat_type), ("Ops", "supergroup"))

    def test_last_activity_is_when_it_was_sent_not_when_the_cron_read_it(self):
        telegram_chats.ingest(message())
        self.assertEqual(TelegramChat.objects.get().last_activity_at,
                         datetime.fromtimestamp(SENT, tz=dt_timezone.utc))

    def test_a_channel_post_registers_its_channel(self):
        telegram_chats.ingest({"update_id": 8, "channel_post": {
            "message_id": 1, "date": SENT, "chat": {"id": -100999, "type": "channel", "title": "News"}}})
        self.assertEqual(TelegramChat.objects.get().chat_type, "channel")

    def test_being_removed_from_a_group_is_recorded(self):
        telegram_chats.ingest(message())
        telegram_chats.ingest({"update_id": 9, "my_chat_member": {
            "chat": GROUP, "date": SENT + 60, "from": {"first_name": "Asha", "username": "asha"},
            "new_chat_member": {"status": "kicked"}}})
        row = TelegramChat.objects.get()
        self.assertFalse(row.in_chat)
        self.assertEqual(row.added_by, "Asha (@asha)")

    def test_seeing_a_chat_again_does_not_duplicate_it(self):
        telegram_chats.ingest(message(7))
        self.assertFalse(telegram_chats.ingest(message(8))["created"])
        self.assertEqual(TelegramChat.objects.count(), 1)

    def test_an_update_with_no_chat_is_ignored(self):
        self.assertEqual(telegram_chats.ingest({"update_id": 1}), {"chat": None, "created": False})
        self.assertEqual(TelegramChat.objects.count(), 0)


@mock.patch("mobileapi.telegram.is_configured", return_value=True)
class ScanTests(TestCase):
    def test_scan_counts_new_chats_and_confirms_what_it_read(self, _configured):
        with mock.patch("mobileapi.telegram.get_updates", return_value=([message(7)], "")) as get:
            result = telegram_chats.scan()
        self.assertEqual(result, {"new": 1, "seen": 0, "updates": 1, "error": ""})
        self.assertEqual(get.call_args.kwargs["offset"], 0)
        self.assertEqual(AppConfig.load().telegram_updates_offset, 8)

    def test_scan_reports_a_telegram_error(self, _configured):
        with mock.patch("mobileapi.telegram.get_updates", return_value=([], "Conflict")):
            self.assertEqual(telegram_chats.scan()["error"], "Conflict")

    def test_the_push_cron_keeps_finding_chats(self, _configured):
        with mock.patch("mobileapi.telegram.get_updates", return_value=([message(7)], "")), \
                mock.patch("mobileapi.telegram.get_chat", return_value=(GROUP, "")), \
                mock.patch("mobileapi.telegram.get_bot_status", return_value="member"), \
                mock.patch("mobileapi.telegram.get_chat_member_count", return_value=12):
            result = cron_views._telegram_tick()
        self.assertEqual((result["new"], result["updates"], result["checked"]), (1, 1, 1))
        row = TelegramChat.objects.get()
        self.assertEqual((row.status, row.member_count), ("member", 12))


class InboxIsGoneTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("root", password="x"))
        cfg = AppConfig.load()
        cfg.telegram_bot_token = "123:test"
        cfg.save()

    def test_its_addresses_are_gone(self):
        self.assertEqual(self.client.get("/mobile/telegram/inbox").status_code, 404)
        self.assertEqual(self.client.get("/mobile/telegram/inbox/list.json").status_code, 404)
        self.assertEqual(self.client.post("/mobile/telegram/live").status_code, 404)
        self.assertEqual(self.client.post("/telegram/hook/anything/", data="{}", content_type="application/json").status_code, 404)

    def test_nothing_stores_messages_any_more(self):
        with self.assertRaises(LookupError):
            apps.get_model("mobileapi", "TelegramMessage")

    def test_the_telegram_page_works_without_it(self):
        TelegramChat.objects.create(chat_id="-100123", title="Ops", status="member")
        r = self.client.get("/mobile/telegram")
        self.assertContains(r, "Chats using this bot")
        self.assertContains(r, "Ops")
        for gone in ("Open inbox", "Incoming messages", "Turn on live"):
            self.assertNotContains(r, gone)

    def test_the_menu_has_no_inbox_and_messages_sits_under_notify(self):
        html = self.client.get("/mobile/telegram").content.decode()
        self.assertNotIn("Telegram inbox", html)
        accounts = html.split('nav-label">Accounts<')[1].split("nav-label")[0]
        notify = html.split('nav-label">Notify<')[1].split("nav-label")[0]
        self.assertNotIn('href="/mobile/chats"', accounts)
        self.assertIn('href="/mobile/chats"', notify)
