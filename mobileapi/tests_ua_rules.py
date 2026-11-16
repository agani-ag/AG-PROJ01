"""Per-link "append to User-Agent" rules (site_tokens.ua_rules_for) sent in /sync and device/hello.
Run: python manage.py test mobileapi.tests_ua_rules"""
import json

from django.core.exceptions import ValidationError
from django.test import TestCase
from unittest import mock

from . import fcm
from .models import AppAccount, AppAuthToken, AppConfig, AppLink, validate_ua_append


def _account(name, email):
    a = AppAccount(name=name, email=email)
    a.set_password("secret123")
    a.save()
    return a


@mock.patch.object(fcm, "is_configured", lambda: False)
class UaRulesTests(TestCase):
    def setUp(self):
        AppConfig.load()
        self.ravi = _account("Ravi", "ravi@example.com")
        self.priya = _account("Priya", "priya@example.com")
        self.bearer = {"HTTP_AUTHORIZATION": f"Bearer {AppAuthToken.issue(self.ravi).key}"}
        # General links: one with an append value, one without.
        AppLink.objects.create(title="General", url="https://general.example.org/", ua_append="SyncUp-G")
        AppLink.objects.create(title="General plain", url="https://plain.example.org/")
        # Ravi's own links; Priya's should never reach Ravi.
        AppLink.objects.create(account=self.ravi, title="Mine", url="https://mine.example.net/a", ua_append="SyncUp-R")
        AppLink.objects.create(account=self.ravi, title="Mine off", url="https://mine-off.example.net/", is_active=False, ua_append="SyncUp-X")
        AppLink.objects.create(account=self.priya, title="Hers", url="https://hers.example.net/", ua_append="SyncUp-P")

    def _sync(self):
        r = self.client.get("/app/v1/sync", **self.bearer)
        self.assertEqual(r.status_code, 200)
        return r.json()

    def test_signed_in_gets_own_and_general_rules_only(self):
        rules = {r["url"]: r["append"] for r in self._sync()["ua_rules"]}
        self.assertEqual(rules, {
            "https://general.example.org/": "SyncUp-G",
            "https://mine.example.net/a": "SyncUp-R",
        })
        # No value → not a rule; inactive → not a rule; another user's link → never.
        self.assertNotIn("https://plain.example.org/", rules)
        self.assertNotIn("https://mine-off.example.net/", rules)
        self.assertNotIn("https://hers.example.net/", rules)

    def test_signed_out_phone_gets_general_rules_only(self):
        r = self.client.post("/app/v1/device/hello", json.dumps({"device_id": "phone-1"}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 200)
        rules = {x["url"]: x["append"] for x in r.json()["ua_rules"]}
        self.assertEqual(rules, {"https://general.example.org/": "SyncUp-G"})

    def test_validator_rejects_line_breaks_and_control_chars(self):
        for bad in ["two\nlines", "tab\there", "bell\x07"]:
            with self.assertRaises(ValidationError):
                validate_ua_append(bad)
        # Plain printable text and blank are fine.
        validate_ua_append("SyncUp-App/1.0 (partner=acme)")
        validate_ua_append("")
