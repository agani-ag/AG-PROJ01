"""Usage time (16 Nov 2026): one number per phone — how long SyncUp has been open on it, signed in or not,
sent by the app with its check-in (device/hello). Run: python manage.py test mobileapi.tests_usage_time"""
import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import AppAccount, AppAuthToken, AppConfig, AppDevice


def _account(name, email):
    a = AppAccount(name=name, email=email)
    a.set_password("secret123")
    a.save()
    return a


class UsageTimeTests(TestCase):
    def setUp(self):
        AppConfig.load()
        self.ravi = _account("Ravi", "ravi@example.com")
        self.token = AppAuthToken.issue(self.ravi)
        self.secret = None

    def hello(self, body, signed_in=True):
        body = {"device_id": "phone-1", **body}
        if self.secret:
            body["device_secret"] = self.secret
        headers = {"HTTP_AUTHORIZATION": f"Bearer {self.token.key}"} if signed_in else {}
        r = self.client.post("/app/v1/device/hello", json.dumps(body), content_type="application/json", **headers)
        self.assertEqual(r.status_code, 200, r.content)
        self.secret = r.json()["device_secret"]
        return AppDevice.objects.get(device_id="phone-1")

    def test_signed_in_hello_stores_the_number(self):
        self.assertEqual(self.hello({"usage_seconds": 3725}).usage_seconds, 3725)
        self.assertEqual(self.hello({"usage_seconds": 4000}).usage_seconds, 4000)

    def test_a_hello_without_it_keeps_the_last_number(self):
        self.hello({"usage_seconds": 600})
        self.assertEqual(self.hello({}).usage_seconds, 600)

    def test_signed_out_hello_stores_it_too(self):
        d = self.hello({"usage_seconds": 999}, signed_in=False)
        self.assertIsNone(d.account)
        self.assertEqual(d.usage_seconds, 999)

    def test_bad_values_are_ignored(self):
        self.hello({"usage_seconds": 120})
        self.assertEqual(self.hello({"usage_seconds": "lots"}).usage_seconds, 120)
        self.assertEqual(self.hello({"usage_seconds": -50}).usage_seconds, 0)

    def test_sign_out_keeps_the_phones_number(self):
        self.hello({"usage_seconds": 900})
        r = self.client.post("/app/v1/devices/unregister", json.dumps({"device_id": "phone-1"}),
                             content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {self.token.key}")
        self.assertEqual(r.status_code, 200)
        d = AppDevice.objects.get(device_id="phone-1")
        self.assertIsNone(d.account)
        self.assertEqual(d.usage_seconds, 900)
        self.assertEqual(self.hello({"usage_seconds": 960}, signed_in=False).usage_seconds, 960)

    def test_text(self):
        d = AppDevice(device_id="x", fcm_token="")
        for seconds, text in [(0, "—"), (59, "—"), (60, "1 m"), (3725, "1 h 02 m"), (36000, "10 h 00 m")]:
            d.usage_seconds = seconds
            self.assertEqual(d.usage_text, text)

    def test_admin_pages_show_it(self):
        device = self.hello({"usage_seconds": 3725})
        self.client.force_login(get_user_model().objects.create_superuser("boss", "boss@example.com", "pw-123456"))
        for url in ["/mobile/devices", f"/mobile/devices/{device.id}", f"/mobile/accounts/{self.ravi.id}"]:
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
            self.assertContains(r, "1 h 02 m", msg_prefix=url)

    # ---- heartbeat (device/beat): every 30 s while the app is open, and once as it closes
    def beat(self, body, secret=None):
        body = {"device_id": "phone-1", "device_secret": self.secret if secret is None else secret, **body}
        return self.client.post("/app/v1/device/beat", json.dumps(body), content_type="application/json")

    def test_beat_while_open_marks_active_now_and_updates_usage(self):
        self.hello({}, signed_in=False)
        self.assertEqual(self.beat({"usage_seconds": 75, "open": True}).status_code, 200)
        d = AppDevice.objects.get(device_id="phone-1")
        self.assertEqual(d.usage_seconds, 75)
        self.assertTrue(d.is_active_now)
        self.assertTrue(AppDevice.objects.filter(AppDevice.active_now_q()).exists())

    def test_closing_beat_clears_active_now_but_keeps_usage(self):
        self.hello({}, signed_in=False)
        self.beat({"usage_seconds": 75, "open": True})
        self.beat({"usage_seconds": 90, "open": False})
        d = AppDevice.objects.get(device_id="phone-1")
        self.assertEqual(d.usage_seconds, 90)
        self.assertIsNone(d.active_at)
        self.assertFalse(d.is_active_now)

    def test_switched_off_beat_has_no_usage(self):
        self.hello({"usage_seconds": 300})
        self.beat({"open": True})
        self.beat({"open": False})
        self.assertEqual(AppDevice.objects.get(device_id="phone-1").usage_seconds, 300)

    def test_old_beat_is_not_active_now(self):
        from datetime import timedelta
        from django.utils import timezone
        self.hello({}, signed_in=False)
        self.beat({"open": True})
        AppDevice.objects.filter(device_id="phone-1").update(active_at=timezone.now() - timedelta(seconds=90))
        self.assertFalse(AppDevice.objects.get(device_id="phone-1").is_active_now)

    def test_beat_needs_the_phones_secret(self):
        self.hello({}, signed_in=False)
        self.assertEqual(self.beat({"open": True}, secret="wrong").status_code, 403)
        self.assertEqual(self.client.post("/app/v1/device/beat", json.dumps({"device_id": "nobody", "open": True}),
                                          content_type="application/json").status_code, 403)
        self.assertIsNone(AppDevice.objects.get(device_id="phone-1").active_at)

    def test_admin_active_now_filter(self):
        self.hello({}, signed_in=False)
        self.beat({"usage_seconds": 61, "open": True})
        self.client.force_login(get_user_model().objects.create_superuser("boss", "boss@example.com", "pw-123456"))
        r = self.client.get("/mobile/devices?state=active_now")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Active now")
        self.assertEqual(r.context["stats"]["active_now"], 1)
        self.assertEqual(len(r.context["page"].object_list), 1)
