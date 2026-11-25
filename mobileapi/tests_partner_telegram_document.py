"""Partner API: POST /partner/v1/telegram/document — a partner's file (e.g. GSTSync's daily database backup)
passed straight through to a Telegram chat on its allowed list. Nothing is kept: the request body goes on
to Telegram a chunk at a time; no file is written and the whole file is never in memory.
Telegram itself is never called.
Run: python manage.py test mobileapi.tests_partner_telegram_document"""
import io
import json
from unittest import mock
from urllib.parse import urlencode

from django.core.cache import cache
from django.test import RequestFactory, TestCase

from . import partner_api, telegram
from .models import AppConfig, AppPartner, AppTelegramLog, PartnerTelegramChat

URL = "/partner/v1/telegram/document"
CHAT = "-1005550001"
DB = b"SQLite format 3" + bytes(1) + b"gst billing data " * 12000  # ~200 KB: several chunks


def setup_partner():
    cache.clear()
    cfg = AppConfig.load()
    cfg.telegram_bot_token = "123:abc"
    cfg.save()
    partner, key = AppPartner.issue("GSTSync")
    PartnerTelegramChat.objects.create(partner=partner, chat_id=CHAT, label="Backups")
    return partner, key


class EndpointTests(TestCase):
    def setUp(self):
        self.partner, self.key = setup_partner()
        self.received = {}

        def stream(chat, filename, source, size, caption, mime):
            self.received = {"bytes": source.read(size), "chat": chat, "filename": filename, "caption": caption, "mime": mime}
            return True, "42"

        self.stream = mock.patch.object(telegram, "stream_document", side_effect=stream).start()
        self.addCleanup(mock.patch.stopall)

    def post(self, body=DB, key=None, content_type="application/octet-stream", **params):
        params.setdefault("chat_id", CHAT)
        return self.client.post(f"{URL}?{urlencode(params)}", data=body, content_type=content_type,
                                HTTP_AUTHORIZATION=f"Bearer {key or self.key}")

    def test_the_body_goes_straight_on_with_its_name_and_caption(self):
        r = self.post(filename="gstbillingdb-2026-11-25.sqlite3", caption="GSTSync backup\n25 Nov 2026 · 7.0 MB / ok")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"success": True, "chat_id": CHAT, "message_id": "42"})
        self.assertEqual(self.received, {"bytes": DB, "chat": CHAT, "filename": "gstbillingdb-2026-11-25.sqlite3",
                                         "caption": "GSTSync backup\n25 Nov 2026 · 7.0 MB / ok", "mime": "application/octet-stream"})

    def test_nothing_is_saved_no_upload_parsing_no_temp_file(self):
        with mock.patch("django.http.multipartparser.MultiPartParser.__init__", side_effect=AssertionError("parsed")), \
                mock.patch("django.core.files.uploadedfile.TemporaryUploadedFile.__init__", side_effect=AssertionError("temp file")), \
                mock.patch("tempfile.NamedTemporaryFile", side_effect=AssertionError("temp file")):
            self.assertEqual(self.post().status_code, 200)
        self.assertEqual(self.received["bytes"], DB)

    def test_crlf_line_breaks_become_plain_ones(self):
        self.post(caption="Line one\r\nLine two")
        self.assertEqual(self.received["caption"], "Line one\nLine two")

    def test_a_filename_is_safe_for_telegram(self):
        for given, shown in (("../../etc/passwd", "passwd"), ("C:\\Users\\x\\db.sqlite3", "db.sqlite3"),
                             ('a"b\nc\x00.db', "abc.db"), ("../", "file"), ("", "file")):
            with self.subTest(given=given):
                self.post(filename=given)
                self.assertEqual(self.received["filename"], shown)

    def test_logged_without_the_file_and_the_chat_marked_used(self):
        self.post(filename="g.sqlite3", caption="note")
        log = AppTelegramLog.objects.get()
        self.assertEqual((log.partner, log.chat_id, log.status, log.message_id), (self.partner, CHAT, "sent", "42"))
        self.assertEqual(log.text, "File: g.sqlite3 (0.2 MB)\nnote")
        self.assertIsNotNone(PartnerTelegramChat.objects.get(chat_id=CHAT).last_used_at)

    def test_only_chats_on_the_partners_list_and_nothing_is_read_otherwise(self):
        r = self.post(chat_id="-100999")
        self.assertEqual((r.status_code, r.json()["success"]), (403, False))
        self.stream.assert_not_called()
        self.assertEqual(AppTelegramLog.objects.get().status, "failed")

    def test_a_partner_allowed_anywhere_may_send_anywhere(self):
        AppPartner.objects.filter(pk=self.partner.pk).update(telegram_allow_any=True)
        self.assertEqual(self.post(chat_id="-100999").status_code, 200)

    def test_telegram_refusing_is_a_502_with_its_reason_and_logged(self):
        self.stream.side_effect = None
        self.stream.return_value = (False, "Bad Request: not enough rights to send documents to the chat")
        r = self.post()
        self.assertEqual((r.status_code, r.json()["error"]), (502, "Bad Request: not enough rights to send documents to the chat"))
        self.assertEqual(AppTelegramLog.objects.get().status, "failed")

    def test_what_is_refused_before_a_byte_is_read(self):
        cases = [
            ({"chat_id": ""}, DB, "application/octet-stream", 400),
            ({"caption": "x" * 1025}, DB, "application/octet-stream", 400),
            ({}, DB, "text/plain", 415),
        ]
        for params, body, ctype, status in cases:
            with self.subTest(params=list(params), ctype=ctype, empty=not body):
                self.assertEqual(self.post(body=body, content_type=ctype, **params).status_code, status)
        with mock.patch.object(telegram, "MAX_DOCUMENT", 100):
            r = self.post()
        self.assertEqual((r.status_code, r.json()["message"]), (413, "file is 0.2 MB; Telegram takes up to 50 MB from a bot"))
        self.stream.assert_not_called()
        self.assertEqual(self.post(caption="x" * 1024).status_code, 200)

    def test_a_multipart_upload_is_refused_not_mangled(self):
        r = self.client.post(f"{URL}?chat_id={CHAT}", {"file": io.BytesIO(DB)}, HTTP_AUTHORIZATION=f"Bearer {self.key}")
        self.assertEqual(r.status_code, 415)
        self.stream.assert_not_called()

    def direct(self, **meta):
        """A request built by hand, for headers the test client always fills in."""
        request = RequestFactory().post(f"{URL}?chat_id={CHAT}", data=DB, content_type="application/octet-stream",
                                        HTTP_AUTHORIZATION=f"Bearer {self.key}")
        for name, value in meta.items():
            if value is None:
                del request.META[name]
            else:
                request.META[name] = value
        return partner_api.telegram_document(request)

    def test_without_content_length_it_asks_for_it(self):
        self.assertEqual(self.direct(CONTENT_LENGTH=None).status_code, 411)
        self.assertEqual(self.direct(CONTENT_LENGTH="lots").status_code, 411)
        self.stream.assert_not_called()

    def test_an_empty_file_is_refused(self):
        r = self.direct(CONTENT_LENGTH="0")
        self.assertEqual((r.status_code, json.loads(r.content)["message"]), (400, "the file is empty"))
        self.stream.assert_not_called()

    def test_the_key_and_method(self):
        self.assertEqual(self.post(key="wrong").status_code, 401)
        self.assertEqual(self.client.get(f"{URL}?chat_id={CHAT}", HTTP_AUTHORIZATION=f"Bearer {self.key}").status_code, 405)
        self.stream.assert_not_called()

    def test_off_when_the_bot_is_not_set_up(self):
        AppConfig.objects.update(telegram_bot_token="")
        self.assertEqual(self.post().status_code, 503)

    def test_one_call_against_the_rate_limit(self):
        AppPartner.objects.filter(pk=self.partner.pk).update(rate_limit_per_min=1)
        self.assertEqual(self.post().status_code, 200)
        self.assertEqual(self.post().status_code, 429)


class StreamTests(TestCase):
    """telegram.stream_document: the body Telegram gets, built while reading, never kept whole."""

    def setUp(self):
        setup_partner()

    def telegram_reply(self, reply, sent):
        def post(url, data, headers, timeout):
            chunks = list(data)  # what the connection would send, chunk by chunk
            sent.update(url=url, chunks=chunks, length=len(data), ctype=headers["Content-Type"])
            r = mock.Mock()
            r.json.return_value = reply
            return r
        session = mock.MagicMock()
        session.__enter__.return_value.post.side_effect = post
        return mock.patch.object(telegram.requests, "Session", return_value=session), session.__enter__.return_value.post

    def test_the_body_is_a_proper_upload_read_a_chunk_at_a_time(self):
        source = io.BytesIO(DB)
        reads = []
        real_read = source.read
        source.read = lambda n=-1: reads.append(n) or real_read(n)
        sent = {}
        patcher, post = self.telegram_reply({"ok": True, "result": {"message_id": 7}}, sent)
        with patcher:
            ok = telegram.stream_document(CHAT, "g.sqlite3", source, len(DB), "note\nline", mime="application/octet-stream")
        self.assertEqual(ok, (True, "7"))
        body = b"".join(sent["chunks"])
        self.assertEqual(sent["length"], len(body))  # Content-Length matches what goes out
        boundary = sent["ctype"].split("boundary=")[1]
        self.assertTrue(body.startswith(f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{CHAT}\r\n'.encode()))
        self.assertIn(b'name="caption"\r\n\r\nnote\nline\r\n', body)
        self.assertIn(b'name="document"; filename="g.sqlite3"\r\nContent-Type: application/octet-stream\r\n\r\n' + DB + f"\r\n--{boundary}--\r\n".encode(), body)
        self.assertTrue(sent["url"].endswith("/bot123:abc/sendDocument"))
        self.assertTrue(all(n <= telegram.STREAM_CHUNK for n in reads))
        self.assertGreater(len(reads), 2)  # in pieces, never the whole file at once

    def test_one_try_only(self):
        sent = {}
        patcher, post = self.telegram_reply({"ok": False, "error_code": 429, "description": "Too Many Requests: retry after 5"}, sent)
        with patcher:
            self.assertEqual(telegram.stream_document(CHAT, "g.db", io.BytesIO(DB), len(DB)), (False, "Too Many Requests: retry after 5"))
        self.assertEqual(post.call_count, 1)

    def test_an_upload_that_stops_short_fails_cleanly(self):
        sent = {}
        patcher, _ = self.telegram_reply({"ok": True, "result": {"message_id": 1}}, sent)
        with patcher:
            ok, error = telegram.stream_document(CHAT, "g.db", io.BytesIO(DB[:1000]), len(DB))
        self.assertFalse(ok)
        self.assertIn("ended before all its bytes arrived", error)
