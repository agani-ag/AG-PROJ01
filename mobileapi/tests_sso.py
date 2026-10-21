"""Sign in with SyncUp — the app's code, SyncHub's check, consent, Signed-in sites, and the admin side.
Run: python manage.py test mobileapi.tests_sso"""
import json
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from . import fcm, sso
from .models import AppAccount, AppAuthToken, AppConfig, AppLink, AppSsoCode, AppSsoIdentity

KEY = "test-sso-key-123"
HOST = "hub.example.com"


@mock.patch.object(fcm, "is_configured", lambda: False)  # never reach Firebase from tests
class SsoTests(TestCase):
    def setUp(self):
        cache.clear()
        cfg = AppConfig.load()
        cfg.sso_key = KEY
        cfg.save()
        self.ravi = AppAccount(name="Ravi", email="ravi@example.com", phone="+91 98765 43210")
        self.ravi.set_password("secret123")
        self.ravi.save()
        self.bearer = {"HTTP_AUTHORIZATION": f"Bearer {AppAuthToken.issue(self.ravi).key}"}
        AppLink.objects.create(title="Hibiscus", url=f"https://{HOST}/hibiscus/", allow_sso=True,
                               external_id="synchub:hibiscus")
        AppLink.objects.create(title="GST Portal", url="https://www.gst.gov.in/")

    def code(self, host=HOST, consent=None, **extra):
        body = {"host": host}
        if consent is not None:
            body["consent"] = consent
        return self.client.post("/app/v1/sso/code", json.dumps(body), content_type="application/json",
                                **(extra or self.bearer))

    def check(self, code, host=HOST, key=KEY):
        return self.client.post("/app/v1/hub/sso/check", json.dumps({"key": key, "code": code, "host": host}),
                                content_type="application/json")

    def signed_in_code(self):
        return self.code(consent=True).json()["code"]

    # ---------------------------------------------------------------- the app's side
    def test_asks_for_consent_once_then_gives_codes(self):
        r = self.code()
        self.assertEqual(r.json(), {"success": True, "needs_consent": True, "client": "SyncHub",
                                    "shares": ["name", "email", "phone"]})
        self.assertFalse(AppSsoCode.objects.exists())
        self.assertTrue(self.code(consent=True).json()["code"])
        self.assertTrue(self.code().json()["code"], "the yes is remembered — later sign-ins are quiet")

    def test_only_sign_in_websites(self):
        self.assertEqual(self.code(host="www.gst.gov.in", consent=True).json()["reason"], "not_allowed")
        self.assertEqual(self.code(host="evil.example.net", consent=True).status_code, 403)
        self.assertEqual(self.code(host=f"https://www.{HOST}/x", consent=True).status_code, 200, "www. and URLs are fine")

    def test_needs_sign_in(self):
        r = self.client.post("/app/v1/sso/code", json.dumps({"host": HOST}), content_type="application/json")
        self.assertEqual(r.status_code, 401)

    def test_sync_and_hello_carry_the_websites_and_the_same_id(self):
        info = self.client.get("/app/v1/sync", **self.bearer).json()["sso"]
        self.assertEqual(info["hosts"], [HOST])
        self.assertTrue(info["id"].startswith("su_"))
        self.assertFalse(info["connected"])
        hello = self.client.post("/app/v1/device/hello", json.dumps({"device_id": "phone-x"}),
                                 content_type="application/json").json()
        self.assertEqual(hello["sso"], {"hosts": [HOST], "id": None, "connected": False}, "signed out: no id")
        self.signed_in_code()
        again = self.client.get("/app/v1/sync", **self.bearer).json()["sso"]
        self.assertEqual(again["id"], info["id"], "the id never changes")
        self.assertTrue(again["connected"])

    # ---------------------------------------------------------------- SyncHub's check
    def test_check_gives_who_it_is(self):
        r = self.check(self.signed_in_code())
        self.assertEqual(r.status_code, 200, r.content)
        user = r.json()["user"]
        self.assertEqual((user["name"], user["email"], user["phone"]), ("Ravi", "ravi@example.com", "9876543210"))
        self.assertEqual(user["id"], AppSsoIdentity.objects.get(account=self.ravi).sub)

    def test_a_code_works_once(self):
        code = self.signed_in_code()
        self.assertEqual(self.check(code).status_code, 200)
        self.assertEqual(self.check(code).json()["reason"], "bad_code")

    def test_wrong_host_burns_the_code(self):
        code = self.signed_in_code()
        self.assertEqual(self.check(code, host="other.example.com").json()["reason"], "wrong_host")
        self.assertEqual(self.check(code).json()["reason"], "bad_code")

    def test_expired(self):
        code = self.signed_in_code()
        AppSsoCode.objects.update(created_at=timezone.now() - timedelta(seconds=61))
        self.assertEqual(self.check(code).json()["reason"], "expired")

    def test_keys(self):
        code = self.signed_in_code()
        self.assertEqual(self.check(code, key="wrong").json()["reason"], "bad_key")
        cfg = AppConfig.load()
        cfg.sso_key = ""
        cfg.save()
        r = self.check(code)
        self.assertEqual((r.status_code, r.json()["reason"]), (503, "off"))
        cfg.links_key = KEY
        cfg.save()
        self.assertEqual(self.check(code).json()["reason"], "off", "the links key is not the sign-in key")

    def test_account_gone_and_disconnected(self):
        code = self.signed_in_code()
        self.client.post("/app/v1/sso/disconnect", **self.bearer)
        self.assertEqual(self.check(code).json()["reason"], "disconnected")
        code = self.signed_in_code()
        AppAccount.objects.filter(pk=self.ravi.pk).update(is_active=False)
        self.assertEqual(self.check(code).json()["reason"], "account_gone")

    # ---------------------------------------------------------------- Signed-in sites
    def test_signed_in_sites_and_disconnect(self):
        self.assertEqual(self.client.get("/app/v1/sso/sites", **self.bearer).json()["sites"], [])
        self.signed_in_code()
        sites = self.client.get("/app/v1/sso/sites", **self.bearer).json()["sites"]
        self.assertEqual((sites[0]["name"], sites[0]["tools"]), ("SyncHub", ["Hibiscus"]))
        self.client.post("/app/v1/sso/disconnect", **self.bearer)
        self.assertEqual(self.client.get("/app/v1/sso/sites", **self.bearer).json()["sites"], [])
        self.assertTrue(self.code().json()["needs_consent"], "asked again after Disconnect")

    # ---------------------------------------------------------------- SyncHub turns it on
    def test_hub_links_sets_the_switch(self):
        cfg = AppConfig.load()
        cfg.links_key = "links-key"
        cfg.save()
        r = self.client.post("/app/v1/hub/links", json.dumps({
            "key": "links-key", "ref": "synchub:pocket", "title": "Pocket Money",
            "url": "https://hub.example.com/pocket/", "sso": True,
        }), content_type="application/json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertTrue(r.json()["link"]["sso"])
        self.assertTrue(AppLink.objects.get(external_id="synchub:pocket").allow_sso)
        bad = self.client.post("/app/v1/hub/links", json.dumps({"key": "links-key", "ref": "synchub:pocket", "sso": "yes"}),
                               content_type="application/json")
        self.assertEqual(bad.status_code, 400)


class SsoAdminTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("boss", "boss@example.com", "pw-123456"))

    def test_key_card_switch_and_counts(self):
        r = self.client.get("/mobile/general-links")
        self.assertContains(r, "SyncUp sign-in key")
        self.client.post("/mobile/general-links/sso-key")
        first = AppConfig.load().sso_key
        self.assertTrue(len(first) > 30)
        self.assertNotEqual(first, AppConfig.load().links_key)
        link = AppLink.objects.create(title="J6", url="https://hub.example.com/j6/")
        self.assertContains(self.client.get(f"/mobile/general-links/{link.id}/edit"), 'name="allow_sso"')
        self.client.post(f"/mobile/general-links/{link.id}/edit", {
            "title": "J6", "url": "https://hub.example.com/j6/", "is_active": "on", "allow_sso": "on",
        })
        link.refresh_from_db()
        self.assertTrue(link.allow_sso)
        self.assertEqual(sso.admin_counts(), {"sign_ins": 0, "people": 0, "disconnected": 0})
