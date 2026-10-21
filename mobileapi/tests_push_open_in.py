"""Admin → Push notifications: where a pushed link opens on the phone (Normal tab or SyncUp tab).
Run: python manage.py test mobileapi.tests_push_open_in"""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from . import fcm
from .models import AppAccount, AppConfig, AppDevice


class PushOpenInTests(TestCase):
    def setUp(self):
        AppConfig.load()
        admin = get_user_model().objects.create_superuser("boss", "boss@example.com", "pw-123456")
        self.client.force_login(admin)
        self.ravi = AppAccount(name="Ravi", email="ravi@example.com")
        self.ravi.set_password("secret123")
        self.ravi.save()
        AppDevice.objects.create(account=self.ravi, device_id="phone-a", fcm_token="tok-a")

    def send(self, **fields):
        body = {"title": "Pay your GST", "body": "Due Friday", "link_url": "https://example.com/gst"}
        body.update(fields)
        with mock.patch.object(fcm, "push", return_value=fcm.PushResult(1, 0, "sent", "", None)) as one, \
                mock.patch.object(fcm, "push_audience", return_value=fcm.PushResult(0, 0, "sent", "", mock.Mock(devices=0))) as many:
            r = self.client.post(reverse("mobile_push"), body)
        self.assertEqual(r.status_code, 302, r.content[:400])
        call = one.call_args if one.called else many.call_args
        return call.kwargs["data"]

    def test_one_account_defaults_to_a_syncup_tab_named_by_the_title(self):
        data = self.send(audience="account", account=self.ravi.pk)
        self.assertEqual(data["open_in"], "syncup")
        self.assertEqual(data["link_title"], "Pay your GST")

    def test_one_account_can_open_in_a_normal_tab(self):
        data = self.send(audience="account", account=self.ravi.pk, open_in="normal")
        self.assertEqual(data["open_in"], "normal")
        self.assertNotIn("link_title", data)

    def test_broadcast_defaults_to_normal_and_can_be_a_named_syncup_tab(self):
        self.assertEqual(self.send(audience="everyone")["open_in"], "normal")
        data = self.send(audience="signed_in", open_in="syncup", link_title="GST Portal")
        self.assertEqual(data["open_in"], "syncup")
        self.assertEqual(data["link_title"], "GST Portal")

    def test_signed_out_always_normal(self):
        self.assertEqual(self.send(audience="signed_out", open_in="syncup")["open_in"], "normal")

    def test_no_link_no_open_in(self):
        with mock.patch.object(fcm, "push_audience", return_value=fcm.PushResult(0, 0, "sent", "", mock.Mock(devices=0))) as many:
            self.client.post(reverse("mobile_push"), {"audience": "everyone", "title": "Hello", "body": "Hi", "open_in": "syncup"})
        self.assertIsNone(many.call_args.kwargs["data"])

    def test_page_shows_the_choice(self):
        r = self.client.get(reverse("mobile_push"))
        self.assertContains(r, 'name="open_in" value="normal"')
        self.assertContains(r, 'name="open_in" value="syncup"')
        self.assertContains(r, "Name in the SyncUp tab")
