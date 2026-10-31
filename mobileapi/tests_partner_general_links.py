"""Partner users see only their partner links: no general links unless the admin switches them on
for that user (AppAccount.general_links; blank = automatic). Run:
python manage.py test mobileapi.tests_partner_general_links"""
import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from . import fcm
from .models import AppAccount, AppAuthToken, AppConfig, AppLink, AppPartner, PartnerConnection
from .site_tokens import enabled_hosts


def _account(name, email, **extra):
    a = AppAccount(name=name, email=email, **extra)
    a.set_password("secret123")
    a.save()
    return a


@mock.patch.object(fcm, "is_configured", lambda: False)  # never reach Firebase from tests
class PartnerGeneralLinksTests(TestCase):
    def setUp(self):
        AppConfig.load()
        self.partner, self.api_key = AppPartner.issue("Acme")
        AppLink.objects.create(title="GST Portal", url="https://www.gst.gov.in/", give_token=True)
        self.ravi = _account("Ravi", "ravi@example.com")  # an admin-made user, no partner

    def _titles(self, account):
        bearer = {"HTTP_AUTHORIZATION": f"Bearer {AppAuthToken.issue(account).key}"}
        return [u["title"] for u in self.client.get("/app/v1/sync", **bearer).json()["urls"]]

    def _connect(self, account, status="enabled"):
        c = PartnerConnection(partner=self.partner, account=account, status=status)
        c.set_password("pw123456")
        c.save()
        return c

    def test_everyone_else_still_gets_general_links(self):
        self.assertEqual(self._titles(self.ravi), ["GST Portal"])
        self.assertIn("gst.gov.in", enabled_hosts(self.ravi))

    def test_user_a_partner_creates_gets_no_general_links(self):
        r = self.client.post("/partner/v1/users", json.dumps({
            "name": "Priya", "email": "priya@example.com", "password": "pw123456",
        }), content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {self.api_key}")
        self.assertIn(r.status_code, (200, 201), r.content)
        priya = AppAccount.objects.get(email="priya@example.com")
        AppLink.objects.create(account=priya, partner=self.partner, title="Acme", url="https://acme.example.com/")
        self.assertIsNone(priya.general_links, "automatic, not a stored choice")
        self.assertEqual(self._titles(priya), ["Acme"])
        self.assertNotIn("gst.gov.in", enabled_hosts(priya), "no token for a general link they can't see")

    def test_user_a_partner_adds_loses_general_links_whatever_the_status(self):
        self._connect(self.ravi, status="not_enabled")
        self.assertEqual(self._titles(self.ravi), [])

    def test_the_app_is_told_who_is_a_partner_user(self):
        bearer = {"HTTP_AUTHORIZATION": f"Bearer {AppAuthToken.issue(self.ravi).key}"}
        self.assertIs(self.client.get("/app/v1/sync", **bearer).json()["user"]["partner_user"], False)
        self._connect(self.ravi)
        self.assertIs(self.client.get("/app/v1/sync", **bearer).json()["user"]["partner_user"], True)
        r = self.client.post("/app/v1/auth/login", json.dumps({"login": "ravi@example.com", "password": "secret123"}),
                             content_type="application/json")
        self.assertIs(r.json()["user"]["partner_user"], True)

    def test_admin_switches_them_on_for_one_partner_user(self):
        self._connect(self.ravi)
        self.ravi.general_links = True
        self.ravi.save()
        self.assertEqual(self._titles(self.ravi), ["GST Portal"])
        self.assertIn("gst.gov.in", enabled_hosts(self.ravi))

    def test_admin_switches_them_off_for_a_normal_user(self):
        self.ravi.general_links = False
        self.ravi.save()
        self.assertEqual(self._titles(self.ravi), [])

    def test_signed_out_phones_still_get_general_links(self):
        self.assertIn("gst.gov.in", enabled_hosts(None))
        self.assertEqual(len(self.client.get("/app/v1/general-links").json()["links"]), 1)


class AccountPageSwitchTests(TestCase):
    """The account page's General links switch: saved as a choice only when it differs from the
    automatic rule, so it keeps following the rule otherwise."""

    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("boss", "boss@example.com", "pw-123456"))
        self.partner, _ = AppPartner.issue("Acme")
        self.ravi = _account("Ravi", "ravi@example.com")

    def _save(self, account, general_on):
        data = {"name": account.name, "email": account.email, "phone": "", "username": "", "is_active": "on"}
        if general_on:
            data["general_links_on"] = "on"
        r = self.client.post(f"/mobile/accounts/{account.id}", data)
        self.assertEqual(r.status_code, 302, r.content[:500])
        account.refresh_from_db()

    def test_saving_unchanged_keeps_automatic(self):
        self._save(self.ravi, general_on=True)
        self.assertIsNone(self.ravi.general_links)

    def test_switching_on_for_a_partner_user_is_remembered(self):
        c = PartnerConnection(partner=self.partner, account=self.ravi)
        c.set_password("pw123456")
        c.save()
        page = self.client.get(f"/mobile/accounts/{self.ravi.id}")
        self.assertContains(page, "Partner user: off by default")
        self._save(self.ravi, general_on=True)
        self.assertIs(self.ravi.general_links, True)
        self._save(self.ravi, general_on=False)
        self.assertIsNone(self.ravi.general_links, "back to automatic (off for a partner user)")

    def test_switching_off_for_a_normal_user(self):
        self._save(self.ravi, general_on=False)
        self.assertIs(self.ravi.general_links, False)
        self.assertContains(self.client.get("/mobile/accounts"), "No general links")
