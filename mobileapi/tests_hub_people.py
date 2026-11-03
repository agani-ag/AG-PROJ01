"""SyncUp → SyncHub people (hub_people.py): who is copied, the tokens, "nothing changed", and who owns a token.
Run: python manage.py test mobileapi.tests_hub_people"""
import json
from unittest import mock

from django.contrib.auth.hashers import check_password
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from . import fcm
from .models import AppAccount, AppAuthToken, AppConfig, AppDevice, AppLink, AppPartner, AppSiteToken, PartnerConnection

KEY = "test-links-key-123"
HUB = "https://hub.example.com/hibiscus/"


def _account(name, email=None, phone=None, **extra):
    a = AppAccount(name=name, email=email, phone=phone, **extra)
    a.set_password("secret123")
    a.save()
    return a


@mock.patch.object(fcm, "is_configured", lambda: False)  # never reach Firebase from tests
class HubPeopleTests(TestCase):
    def setUp(self):
        cache.clear()
        cfg = AppConfig.load()
        cfg.links_key = KEY
        cfg.save()
        # SyncHub's tool, added the way SyncHub adds it (hub/links), giving the page token.
        AppLink.objects.create(title="Hibiscus", url=HUB, external_id="synchub:hibiscus", give_token=True)
        self.ravi = _account("Ravi Kumar", "ravi@example.com", "+919840011223")
        self.meena = _account("Meena", phone="+919840099001", source="self")
        self.partner, _ = AppPartner.issue("Acme")

    def people(self, **body):
        body.setdefault("key", KEY)
        return self.client.post("/app/v1/hub/people", json.dumps(body), content_type="application/json")

    def who(self, token, key=KEY):
        return self.client.post("/app/v1/hub/people/who", json.dumps({"key": key, "token": token}), content_type="application/json")

    def connect(self, account, status="enabled"):
        c = PartnerConnection(partner=self.partner, account=account, status=status)
        c.set_password("pw123456")
        c.save()
        return c

    def app_token(self, account):
        """The token the app puts on SyncHub's pages for this person (GET /sync)."""
        bearer = {"HTTP_AUTHORIZATION": f"Bearer {AppAuthToken.issue(account).key}"}
        sites = self.client.get("/app/v1/sync", **bearer).json()["token_sites"]
        return {s["host"]: s["token"] for s in sites}.get("hub.example.com")

    def listed(self, r=None):
        r = r or self.people()
        self.assertEqual(r.status_code, 200, r.content)
        return {p["name"]: p for p in r.json()["people"]}

    # ---- the key

    def test_needs_the_links_key(self):
        self.assertEqual(self.people(key="wrong").status_code, 403)
        self.assertEqual(self.people(key="").status_code, 403)
        cfg = AppConfig.load()
        cfg.links_key = ""
        cfg.save()
        self.assertEqual(self.people().status_code, 503)
        self.assertEqual(self.who("su_x").status_code, 503)

    def test_post_only_so_the_key_never_sits_in_an_address(self):
        self.assertEqual(self.client.get(f"/app/v1/hub/people?key={KEY}").status_code, 405)
        self.assertEqual(self.client.get(f"/app/v1/hub/people/who?key={KEY}&token=x").status_code, 405)

    def test_bad_json(self):
        r = self.client.post("/app/v1/hub/people", "not json", content_type="application/json")
        self.assertEqual(r.status_code, 400)

    # ---- who is copied

    def test_everyone_who_sees_general_links_with_what_syncHub_needs(self):
        data = self.people().json()
        self.assertEqual((data["host"], data["token_on"]), ("hub.example.com", True))
        people = self.listed()
        self.assertEqual(set(people), {"Ravi Kumar", "Meena"})
        ravi = people["Ravi Kumar"]
        self.assertEqual(set(ravi), {"id", "name", "email", "phone", "password", "token"})
        self.assertEqual((ravi["id"], ravi["email"], ravi["phone"]), (str(self.ravi.id), "ravi@example.com", "+919840011223"))
        self.assertTrue(check_password("secret123", ravi["password"]), "the hash works with Django's check_password")
        self.assertEqual(people["Meena"]["email"], "", "no email is a blank, not null")

    def test_partner_users_only_when_given_general_links(self):
        priya = _account("Priya", "priya@example.com", source="partner")
        added = _account("Arun", "arun@example.com")
        self.connect(added, status="not_enabled")  # added by a partner: a partner user whatever the status
        self.assertNotIn("Priya", self.listed())
        self.assertNotIn("Arun", self.listed())
        priya.general_links = True
        priya.save()
        self.assertIn("Priya", self.listed())
        self.ravi.general_links = False  # the admin's own choice for a non-partner user counts too
        self.ravi.save()
        self.assertNotIn("Ravi Kumar", self.listed())

    def test_turned_off_or_deleted_people_drop_out(self):
        self.ravi.is_active = False
        self.ravi.save()
        self.assertNotIn("Ravi Kumar", self.listed())
        self.meena.delete()
        self.assertEqual(self.listed(), {})

    # ---- tokens

    def test_the_token_is_the_one_the_app_puts_on_synchubs_pages(self):
        people = self.listed()
        self.assertTrue(people["Ravi Kumar"]["token"].startswith("su_"))
        self.assertEqual(people["Ravi Kumar"]["token"], self.app_token(self.ravi), "list first, then the app: the same")
        self.assertNotEqual(people["Ravi Kumar"]["token"], people["Meena"]["token"])
        priya = _account("Priya", "priya@example.com")
        app_first = self.app_token(priya)
        self.assertEqual(self.listed()["Priya"]["token"], app_first, "app first, then the list: the same")
        self.assertEqual(AppSiteToken.objects.filter(account=priya).count(), 1)

    def test_no_tokens_while_synchubs_links_dont_give_one(self):
        AppLink.objects.filter(external_id="synchub:hibiscus").update(give_token=False)
        data = self.people().json()
        self.assertFalse(data["token_on"])
        self.assertEqual({p["token"] for p in data["people"]}, {""})
        self.assertFalse(AppSiteToken.objects.exists())

    def test_before_synchub_added_anything(self):
        AppLink.objects.all().delete()
        data = self.people().json()
        self.assertEqual((data["host"], data["token_on"]), ("", False))
        self.assertEqual(len(data["people"]), 2, "people still come; only tokens wait")

    # ---- "nothing changed"

    def test_nothing_changed_until_something_does(self):
        version = self.people().json()["version"]
        self.assertEqual(self.people(version=version).json(), {"success": True, "unchanged": True, "version": version})
        self.ravi.name = "Ravi K"
        self.ravi.save()
        r = self.people(version=version).json()
        self.assertNotEqual(r["version"], version)
        self.assertIn("Ravi K", {p["name"] for p in r["people"]})
        version = r["version"]
        self.ravi.set_password("changed99")  # a password change reaches SyncHub too
        self.ravi.save()
        self.assertNotEqual(self.people(version=version).json()["version"], version)

    def test_a_partner_connection_alone_changes_the_list(self):
        version = self.people().json()["version"]
        self.connect(self.meena)  # Meena becomes a partner user: no General links any more
        r = self.people(version=version).json()
        self.assertNotIn("unchanged", r)
        self.assertNotIn("Meena", {p["name"] for p in r["people"]})

    # ---- who owns a token

    def test_who_a_signed_in_person_is(self):
        token = self.app_token(self.ravi)
        r = self.who(token)
        self.assertEqual(r.status_code, 200)
        p = r.json()["person"]
        self.assertEqual((p["id"], p["name"], p["token"]), (str(self.ravi.id), "Ravi Kumar", token))
        self.assertEqual(p, self.listed()["Ravi Kumar"], "the same fields as the list")

    def test_a_signed_out_phone_is_never_a_person(self):
        device = AppDevice.objects.create(device_id="phone-1", fcm_token="f")
        token = AppSiteToken.objects.create(host="hub.example.com", device=device, token="su_phone").token
        self.assertEqual(self.who(token).json(), {"success": True, "person": None, "reason": "phone"})

    def test_someone_not_on_the_list(self):
        token = self.app_token(self.meena)
        self.connect(self.meena)
        self.assertEqual(self.who(token).json(), {"success": True, "person": None, "reason": "not_included"})
        self.meena.general_links = True
        self.meena.save()
        self.assertEqual(self.who(token).json()["person"]["name"], "Meena")
        self.meena.is_active = False
        self.meena.save()
        self.assertEqual(self.who(token).json()["reason"], "not_included")

    def test_tokens_for_other_websites_and_rubbish_are_unknown(self):
        other = AppSiteToken.objects.create(host="elsewhere.example.org", account=self.ravi, token="su_other").token
        for token in (other, "su_nope", "", None, 123, "signed:old-style"):
            r = self.who(token)
            self.assertEqual(r.status_code, 404, token)
            self.assertEqual(r.json(), {"success": False, "error": "Unknown token"})

    def test_who_needs_the_key(self):
        token = self.app_token(self.ravi)
        self.assertEqual(self.who(token, key="wrong").status_code, 403)

    def test_too_many_calls(self):
        with mock.patch("mobileapi.hub_people._rate_limited", return_value=True):
            self.assertEqual(self.people().status_code, 429)
            self.assertEqual(self.who("su_x").status_code, 429)

    # ---- the admin page

    def test_the_key_card_says_it_reads_people(self):
        admin = get_user_model().objects.create_superuser("hub-admin", "a@example.invalid", "x")
        self.client.force_login(admin)
        page = self.client.get("/mobile/general-links")
        self.assertContains(page, "copy SyncUp's people into SyncHub")
        self.assertContains(page, "(2 now)")
