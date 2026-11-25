"""The daily database backup to Telegram: sent after the cleanup cron has answered, once a day, to the
chat picked on the Telegram page; plus Backup now. Telegram itself is never called.
Run: python manage.py test mobileapi.tests_telegram_backup"""
import json
import os
import sqlite3
import tempfile
import time
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import cron_views, telegram, telegram_backup
from .models import AppAccount, AppConfig, AppTelegramLog, CronLock, TelegramChat

GROUP = "-1001234567890"
FAKE_DB = b"SQLite format 3" + bytes(1) + b"stand-in"


def configure(chat=GROUP):
    cfg = AppConfig.load()
    cfg.telegram_bot_token = "123:abc"
    cfg.telegram_backup_chat_id = chat
    cfg.save()


def connection_name():
    from django.db import connection
    return str(connection.settings_dict["NAME"])


def open_backup(data):
    """The sent file, opened as it is: {table: row count} for the tables asked about."""
    fd, path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    try:
        with open(path, "wb") as f:
            f.write(data)
        db = sqlite3.connect(path)
        try:
            return {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    for t in ("mobileapi_appaccount", "django_session", "auth_user")}
        finally:
            db.close()
    finally:
        os.remove(path)


@override_settings(CRON_KEY="k")
class CronBackupTests(TestCase):
    def setUp(self):
        AppAccount.objects.create(name="Asha", email="a@example.org")
        AppAccount.objects.create(name="Bala", email="b@example.org")
        self.send = mock.patch.object(telegram, "send_document", return_value=(True, "77")).start()
        mock.patch.object(telegram_backup, "_snapshot", return_value=FAKE_DB).start()
        self.addCleanup(mock.patch.stopall)

    def cleanup(self):
        return self.client.get("/cron/cleanup", HTTP_X_CRON_KEY="k")

    def test_off_until_a_chat_is_picked(self):
        configure(chat="")
        self.assertEqual(self.cleanup().json()["backup"], "off")
        self.send.assert_not_called()

    def test_the_reply_goes_first_and_the_backup_after_it(self):
        configure()
        request = RequestFactory().get("/cron/cleanup", HTTP_X_CRON_KEY="k")
        request.user = mock.Mock(is_authenticated=False)
        response = cron_views.cleanup(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)["backup"], "sending after this reply")
        self.send.assert_not_called()        # the reply is complete, nothing sent yet
        response.close()                     # what the server does once the reply has gone out
        self.send.assert_called_once()
        self.assertEqual(self.send.call_args.args[0], GROUP)

    def test_once_a_day_however_often_the_cron_runs(self):
        configure()
        self.assertEqual(self.cleanup().json()["backup"], "sending after this reply")
        self.assertEqual(self.cleanup().json()["backup"], "sent today")
        self.assertEqual(self.send.call_count, 1)
        lock = CronLock.objects.get(name=telegram_backup.BACKUP_JOB)
        self.assertAlmostEqual((lock.locked_until - timezone.now()).total_seconds(), 20 * 3600, delta=60)

    def test_the_next_day_sends_again(self):
        configure()
        self.cleanup()
        CronLock.objects.filter(name=telegram_backup.BACKUP_JOB).update(locked_until=timezone.now() - timedelta(seconds=1))
        self.cleanup()
        self.assertEqual(self.send.call_count, 2)

    def test_a_failed_send_tries_again_on_the_next_call(self):
        configure()
        self.send.return_value = (False, "Bad Request: chat not found")
        self.cleanup()
        self.assertEqual(self.cleanup().json()["backup"], "sending after this reply")
        self.assertEqual(self.send.call_count, 2)
        self.assertEqual(AppTelegramLog.objects.filter(status="failed").first().error, "Bad Request: chat not found")

    def test_a_crash_while_copying_never_breaks_the_cron(self):
        configure()
        with mock.patch.object(telegram_backup, "_snapshot", side_effect=OSError("disk full")):
            r = self.cleanup()
        self.assertEqual(r.status_code, 200)
        self.send.assert_not_called()
        self.assertIn("disk full", AppTelegramLog.objects.get().error)
        self.assertEqual(telegram_backup.due(), "sending after this reply")  # free for the next call

    def test_a_locked_cleanup_sends_nothing(self):
        configure()
        CronLock.acquire(cron_views.CLEANUP_JOB, 600)
        self.assertEqual(self.cleanup().json(), {"ok": True, "skipped": "locked"})
        self.send.assert_not_called()

    def test_without_the_cron_key_nothing_happens(self):
        configure()
        self.assertEqual(self.client.get("/cron/cleanup").status_code, 404)
        self.send.assert_not_called()


class BackupFileTests(TransactionTestCase):
    """A real copy, so no test transaction may hold the database open."""

    def setUp(self):
        configure()
        boss = get_user_model().objects.create_superuser("boss", password="pw")
        self.client.force_login(boss)  # an admin session in the live database
        AppAccount.objects.create(name="Asha", email="a@example.org")
        AppAccount.objects.create(name="Bala", email="b@example.org")
        self.send = mock.patch.object(telegram, "send_document", return_value=(True, "77")).start()
        self.addCleanup(mock.patch.stopall)

    def test_the_file_is_the_whole_database_as_it_is_under_its_own_name(self):
        with mock.patch.object(telegram_backup, "db_file", return_value="ag-proj01.sqlite3"):
            ok, _ = telegram_backup.send()
        self.assertTrue(ok)
        chat, name, data, note = self.send.call_args.args
        self.assertEqual(name, "ag-proj01.sqlite3")
        self.assertTrue(data.startswith(b"SQLite format 3\x00"))  # a plain .sqlite3, not zipped
        self.assertEqual(open_backup(data), {"mobileapi_appaccount": 2, "django_session": 1, "auth_user": 1})
        self.assertEqual(self.send.call_args.kwargs["mime"], "application/vnd.sqlite3")

    def test_the_note_says_when_how_big_and_whats_in_it_and_nothing_else(self):
        with mock.patch.object(telegram_backup, "db_file", return_value="ag-proj01.sqlite3"):
            telegram_backup.send()
        note = self.send.call_args.args[3]
        lines = note.splitlines()
        self.assertEqual(len(lines), 6)
        self.assertEqual(lines[0], "SyncUp database backup")
        self.assertRegex(lines[1], r"^\d{2} \w{3} \d{4}, \d{1,2}:\d{2} [AP]M IST$")
        self.assertEqual(lines[2], "")
        self.assertRegex(lines[3], r"^ag-proj01\.sqlite3, [\d.]+ [KM]B$")
        self.assertTrue(lines[4].startswith("Accounts 2 · Devices 0 · Links 0"))
        self.assertTrue(lines[5].startswith("Reminders 0 · Chat messages 0 · Synced items 0"))
        for gone in ("http", "pythonanywhere", "testserver", "restore", "Reload"):
            self.assertNotIn(gone, note)

    def test_a_database_busy_too_long_fails_the_backup_instead_of_hanging(self):
        # The copy reads a file another connection holds locked, the way a long write would.
        fd, busy = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        held = sqlite3.connect(busy)
        held.execute("CREATE TABLE t (x)")
        held.commit()
        held.execute("BEGIN EXCLUSIVE")
        real = sqlite3.connect

        def connect(name, *args, **kwargs):
            return real(busy, timeout=0) if name == connection_name() else real(name, *args, **kwargs)

        try:
            with mock.patch.object(telegram_backup, "COPY_TIMEOUT", 0.3),                     mock.patch.object(telegram_backup.sqlite3, "connect", side_effect=connect):
                started = time.monotonic()
                ok, error = telegram_backup.send()
        finally:
            held.rollback()
            held.close()
            os.remove(busy)
        self.assertFalse(ok)
        self.assertIn("stayed busy", error)
        self.assertLess(time.monotonic() - started, 5)
        self.send.assert_not_called()

    def test_each_backup_is_in_the_send_log(self):
        telegram_backup.send()
        log = AppTelegramLog.objects.get()
        self.assertEqual((log.chat_id, log.status, log.message_id), (GROUP, "sent", "77"))
        self.assertTrue(log.text.startswith("SyncUp database backup"))


class SendDocumentTests(TestCase):
    def setUp(self):
        configure()

    def reply(self, body):
        r = mock.Mock()
        r.json.return_value = body
        return r

    def post(self, *replies):
        session = mock.MagicMock()
        session.__enter__.return_value.post.side_effect = list(replies)
        return mock.patch.object(telegram, "_session", return_value=session), session.__enter__.return_value.post

    def test_uploads_the_file_with_its_caption(self):
        patcher, post = self.post(self.reply({"ok": True, "result": {"message_id": 9}}))
        with patcher:
            self.assertEqual(telegram.send_document(GROUP, "b.sqlite3", b"data", "note", mime="application/vnd.sqlite3"), (True, "9"))
        kwargs = post.call_args.kwargs
        self.assertTrue(post.call_args.args[0].endswith("/bot123:abc/sendDocument"))
        self.assertEqual(kwargs["data"], {"chat_id": GROUP, "caption": "note"})
        self.assertEqual(kwargs["files"]["document"], ("b.sqlite3", b"data", "application/vnd.sqlite3"))

    def test_too_big_is_refused_without_asking_telegram(self):
        patcher, post = self.post()
        with patcher:
            ok, error = telegram.send_document(GROUP, "b.gz", b"x" * (telegram.MAX_DOCUMENT + 1))
        self.assertFalse(ok)
        self.assertIn("50 MB", error)
        post.assert_not_called()

    def test_busy_telegram_is_tried_three_times_and_a_bad_chat_once(self):
        busy = self.reply({"ok": False, "error_code": 429, "description": "Too Many Requests", "parameters": {"retry_after": 0}})
        patcher, post = self.post(busy, busy, busy)
        with patcher, mock.patch.object(telegram.time, "sleep"):
            self.assertEqual(telegram.send_document(GROUP, "b.gz", b"d"), (False, "Too Many Requests"))
        self.assertEqual(post.call_count, 3)
        patcher, post = self.post(self.reply({"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}))
        with patcher:
            self.assertEqual(telegram.send_document(GROUP, "b.gz", b"d"), (False, "Bad Request: chat not found"))
        self.assertEqual(post.call_count, 1)


class TelegramPageTests(TestCase):
    def setUp(self):
        configure(chat="")
        self.client.force_login(get_user_model().objects.create_superuser("boss", password="pw"))
        TelegramChat.objects.create(chat_id=GROUP, title="Backups", chat_type="supergroup", status="administrator", member_count=1)
        TelegramChat.objects.create(chat_id="-1009", title="Old group", chat_type="group", status="kicked")
        TelegramChat.objects.create(chat_id="555", title="Me", chat_type="private", status="member")

    def save(self, chat):
        return self.client.post(reverse("mobile_telegram_backup_save"), {"telegram_backup_chat_id": chat})

    def test_the_card_offers_only_chats_the_bot_is_in(self):
        with mock.patch.object(telegram_backup, "db_file", return_value="ag-proj01.sqlite3"):
            page = self.client.get(reverse("mobile_telegram")).content.decode()
        self.assertIn("Daily database backup", page)
        self.assertIn(f'<option value="{GROUP}">Backups · Group · 1 people</option>', page)
        self.assertIn('<option value="555">Me · Person</option>', page)
        self.assertNotIn("Old group ·", page)
        self.assertIn("Everyone in that chat gets the whole database", page)
        self.assertIn("<code>ag-proj01.sqlite3</code> itself", page)

    def test_picking_a_chat_turns_it_on_and_off_again(self):
        self.save(GROUP)
        self.assertEqual(AppConfig.load().telegram_backup_chat_id, GROUP)
        self.assertContains(self.client.get(reverse("mobile_telegram")), f'<option value="{GROUP}" selected>')
        self.save("")
        self.assertEqual(AppConfig.load().telegram_backup_chat_id, "")

    def test_a_chat_the_bot_is_not_in_is_refused(self):
        for bad in ("-1009", "-100777", "<script>"):
            with self.subTest(bad=bad):
                self.save(bad)
                self.assertEqual(AppConfig.load().telegram_backup_chat_id, "")

    def test_backup_now_sends_straight_away_and_leaves_the_daily_one(self):
        self.save(GROUP)
        mock.patch.object(telegram_backup, "_snapshot", return_value=FAKE_DB).start()
        self.addCleanup(mock.patch.stopall)
        page = self.client.get(reverse("mobile_telegram")).content.decode()
        self.assertRegex(page, r'<button type="submit" class="btn btn-sm">.*Backup now</button>')
        with mock.patch.object(telegram, "send_document", return_value=(True, "5")) as send:
            r = self.client.post(reverse("mobile_telegram_backup_now"), follow=True)
        send.assert_called_once()
        self.assertContains(r, "Backup sent")
        self.assertEqual(telegram_backup.due(), "sending after this reply")

    def test_backup_now_waits_for_a_chat(self):
        self.assertRegex(self.client.get(reverse("mobile_telegram")).content.decode(), r"disabled title=\"Pick a chat and save first\">.*Backup now")
        with mock.patch.object(telegram, "send_document") as send:
            r = self.client.post(reverse("mobile_telegram_backup_now"), follow=True)
        send.assert_not_called()
        self.assertContains(r, "Pick a chat for backups first")

    def test_only_the_admin(self):
        self.client.logout()
        with mock.patch.object(telegram, "send_document") as send:
            self.client.post(reverse("mobile_telegram_backup_now"))
            self.save(GROUP)
        send.assert_not_called()
        self.assertEqual(AppConfig.load().telegram_backup_chat_id, "")
