"""Tools → Wellbeing through the account sync: the new kinds, their caps, pings and admin counts.
Run: python manage.py test mobileapi.tests_wellbeing_sync"""
import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from . import browser_sync, fcm
from .models import AppAccount, AppAuthToken, AppConfig, AppDevice, SyncItem


class WellbeingSyncTests(TestCase):
    def setUp(self):
        AppConfig.load()
        self.ravi = AppAccount(name="Ravi", email="ravi@example.com")
        self.ravi.set_password("secret123")
        self.ravi.save()
        self.bearer = {"HTTP_AUTHORIZATION": f"Bearer {AppAuthToken.issue(self.ravi).key}"}
        AppDevice.objects.create(account=self.ravi, device_id="phone-b", fcm_token="tok-b")

    def sync(self, changes, kinds, device="phone-a"):
        body = {"device_id": device, "since": "", "changes": changes, "kinds": kinds}
        r = self.client.post("/app/v1/browser/sync", json.dumps(body), content_type="application/json", **self.bearer)
        self.assertEqual(r.status_code, 200, r.content[:300])
        return r.json()

    def test_every_wellbeing_kind_round_trips(self):
        now = 1_760_000_000_000
        kinds = sorted(browser_sync.WELLBEING_KINDS)
        changes = [{"kind": k, "key": f"{k}-1", "data": {"v": 1}, "updated_ms": now, "deleted": False} for k in kinds]
        changes.append({"kind": "water", "key": "settings", "data": {"goal": 2000}, "updated_ms": now, "deleted": False})
        with mock.patch.object(fcm, "is_configured", lambda: False):
            self.sync(changes, kinds)
        back = self.sync([], kinds, device="phone-b")["changes"]
        self.assertEqual(sorted((c["kind"], c["key"]) for c in back),
                         sorted([(k, f"{k}-1") for k in kinds] + [("water", "settings")]))

    def test_wellbeing_changes_ping_other_phones_but_steps_dont(self):
        now = 1_760_000_000_000
        with mock.patch.object(fcm, "is_configured", lambda: True), mock.patch.object(fcm, "send_silent") as ping:
            self.sync([{"kind": "steps", "key": "2026-10-20/phone-a", "data": {"steps": 4000}, "updated_ms": now}], ["steps"])
            self.assertFalse(ping.called)
            self.sync([{"kind": "mood", "key": "m1", "data": {"mood": "good"}, "updated_ms": now}], ["mood"])
            ping.assert_called_once()
            self.assertEqual(ping.call_args.args[1], {"type": "sync", "what": "wellbeing"})

    def test_calendar_still_pings_calendar(self):
        with mock.patch.object(fcm, "is_configured", lambda: True), mock.patch.object(fcm, "send_silent") as ping:
            self.sync([{"kind": "task", "key": "t1", "data": {"title": "x"}, "updated_ms": 1}], ["task"])
        self.assertEqual(ping.call_args.args[1]["what"], "calendar")

    def test_old_drinks_are_trimmed_but_settings_stay(self):
        SyncItem.objects.create(account=self.ravi, kind="water", key="old", data={"ml": 250}, updated_ms=1)
        SyncItem.objects.create(account=self.ravi, kind="water", key="settings", data={"goal": 2000}, updated_ms=1)
        with mock.patch.object(fcm, "is_configured", lambda: False):
            self.sync([], ["water"])
        self.assertEqual(list(SyncItem.objects.filter(kind="water").values_list("key", flat=True)), ["settings"])

    def test_admin_account_page_shows_counts_only(self):
        SyncItem.objects.create(account=self.ravi, kind="mood", key="m1", data={"note": "secret note"}, updated_ms=1)
        SyncItem.objects.create(account=self.ravi, kind="habit", key="h1", data={"name": "Read"}, updated_ms=1)
        self.client.force_login(get_user_model().objects.create_superuser("boss", "boss@example.com", "pw-123456"))
        r = self.client.get(f"/mobile/accounts/{self.ravi.pk}")
        self.assertContains(r, "1 mood")
        self.assertContains(r, "1 habit")
        self.assertNotContains(r, "secret note")
