"""Page tokens only on switched-on websites (site_tokens.py) and the app's Notifications page
(notifications_api.py).  Run: python manage.py test mobileapi.tests_tokens_notifications"""
import json
from datetime import timedelta
from unittest import mock

from django.core import signing
from django.test import TestCase, override_settings
from django.utils import timezone

from . import fcm
from .models import (
    AppAccount, AppAuthToken, AppConfig, AppDevice, AppLink, AppNotificationLog, AppNotificationRead,
    AppPartner, HomeShortcut, PartnerConnection, ShortcutCategory,
)
from .serializers import PARTNER_NOTIFY_SALT

KEY = "test-notify-key"


def _account(name, email):
    a = AppAccount(name=name, email=email)
    a.set_password("secret123")
    a.save()
    return a


@override_settings(SYNCUP_NOTIFY_KEY=KEY)
@mock.patch.object(fcm, "is_configured", lambda: False)  # never reach Firebase from tests
class TokenSitesTests(TestCase):
    def setUp(self):
        AppConfig.load()
        self.ravi = _account("Ravi", "ravi@example.com")
        self.priya = _account("Priya", "priya@example.com")
        self.bearer = {"HTTP_AUTHORIZATION": f"Bearer {AppAuthToken.issue(self.ravi).key}"}
        cat = ShortcutCategory.objects.create(name="Health")
        HomeShortcut.objects.create(category=cat, title="Portal", url="https://www.Portal.example.com/x", give_token=True)
        HomeShortcut.objects.create(category=cat, title="Plain", url="https://plain.example.com/", give_token=False)
        AppLink.objects.create(title="General", url="https://general.example.org/", give_token=True)
        AppLink.objects.create(title="General off", url="https://general-off.example.org/")
        self.mine = AppLink.objects.create(account=self.ravi, title="Mine", url="https://mine.example.net/a", give_token=True)
        AppLink.objects.create(account=self.priya, title="Hers", url="https://hers.example.net/", give_token=True)
        AppLink.objects.create(account=self.ravi, title="Mine off", url="https://mine-off.example.net/")

    def _sync(self):
        r = self.client.get("/app/v1/sync", **self.bearer)
        self.assertEqual(r.status_code, 200)
        return r.json()

    def _notify(self, token, key=KEY):
        return self.client.post("/app/v1/partner/notify", json.dumps({"key": key, "token": token, "title": "Hi"}),
                                content_type="application/json")

    def test_signed_in_gets_only_switched_on_websites(self):
        data = self._sync()
        sites = {s["host"]: s["token"] for s in data["token_sites"]}
        self.assertEqual(set(sites), {"portal.example.com", "general.example.org", "mine.example.net"})
        self.assertTrue(all(t.startswith("su_") for t in sites.values()))
        self.assertEqual(len(set(sites.values())), 3, "a different token per website")
        self.assertEqual({s["host"]: s["token"] for s in self._sync()["token_sites"]}, sites, "stable across syncs")
        links = {u["title"]: u for u in data["urls"]}
        self.assertEqual(links["Mine"]["notify_token"], sites["mine.example.net"])  # app v6
        self.assertEqual(links["Mine off"]["notify_token"], "")
        self.assertNotIn("notify_token", data["user"])

    def test_signed_out_phone_gets_shortcut_and_general_link_websites(self):
        r = self.client.post("/app/v1/device/hello", json.dumps({"device_id": "phone-1"}), content_type="application/json")
        self.assertEqual(r.status_code, 200)
        sites = r.json()["token_sites"]
        self.assertEqual([s["host"] for s in sites], ["general.example.org", "portal.example.com"])
        sites = [s for s in sites if s["host"] == "portal.example.com"]
        ravi_token = {s["host"]: s["token"] for s in self._sync()["token_sites"]}["portal.example.com"]
        self.assertNotEqual(sites[0]["token"], ravi_token, "phone token differs from the user's")

    def test_push_by_site_token_user_and_phone(self):
        token = {s["host"]: s["token"] for s in self._sync()["token_sites"]}["mine.example.net"]
        self.assertEqual(self._notify(token).status_code, 200)
        log = AppNotificationLog.objects.latest("id")
        self.assertEqual((log.account, log.site, log.source), (self.ravi, "mine.example.net", "partner_token"))
        self.assertEqual(self._notify(token, key="wrong").status_code, 403)

        hello = self.client.post("/app/v1/device/hello", json.dumps({"device_id": "phone-2"}),
                                 content_type="application/json").json()
        self.assertEqual(self._notify(hello["token_sites"][0]["token"]).status_code, 200)
        log = AppNotificationLog.objects.latest("id")
        self.assertEqual((log.account, log.device.device_id), (None, "phone-2"))

    def test_reset_makes_saved_tokens_stop(self):
        from . import site_tokens
        token = {s["host"]: s["token"] for s in self._sync()["token_sites"]}["general.example.org"]
        self.assertEqual(site_tokens.reset_host("general.example.org"), 1)
        self.assertEqual(self._notify(token).status_code, 403)
        fresh = {s["host"]: s["token"] for s in self._sync()["token_sites"]}["general.example.org"]
        self.assertNotEqual(fresh, token)

    def test_old_tokens_work_for_60_days_then_stop(self):
        old = signing.dumps({"account_id": self.ravi.id}, salt=PARTNER_NOTIFY_SALT)
        self.assertEqual(self._notify(old).status_code, 200)
        until = AppConfig.load().legacy_page_tokens_until
        self.assertEqual(until, timezone.localdate() + timedelta(days=60))
        AppConfig.objects.update(legacy_page_tokens_until=timezone.localdate() - timedelta(days=1))
        self.assertEqual(self._notify(old).status_code, 403)

    def test_partner_api_can_switch_token_on(self):
        partner, api_key = AppPartner.issue("Acme")
        c = PartnerConnection(partner=partner, account=self.priya, status="enabled")
        c.set_password("pw123456")
        c.save()
        r = self.client.post(f"/partner/v1/users/{self.priya.id}/links",
                             json.dumps({"title": "Acme", "url": "https://acme.example.com/", "token": True}),
                             content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {api_key}")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertTrue(r.json()["link"]["token"])
        self.assertTrue(AppLink.objects.get(url="https://acme.example.com/").give_token)


@mock.patch.object(fcm, "is_configured", lambda: False)
class NotificationsTests(TestCase):
    def setUp(self):
        self.ravi = _account("Ravi", "ravi@example.com")
        self.bearer = {"HTTP_AUTHORIZATION": f"Bearer {AppAuthToken.issue(self.ravi).key}"}
        hello = self.client.post("/app/v1/device/hello", json.dumps({"device_id": "phone-x"}),
                                 content_type="application/json").json()
        self.phone = AppDevice.objects.get(device_id="phone-x")
        self.phone_auth = {"HTTP_X_DEVICE_ID": "phone-x", "HTTP_X_DEVICE_SECRET": hello["device_secret"]}

        def log(title, **kw):
            return AppNotificationLog.objects.create(title=title, body="b", **kw)

        self.direct = log("To Ravi", account=self.ravi, source="admin", data={"link_url": "https://x.example.com"})
        self.everyone = log("Everyone", source="admin", audience="everyone")
        self.users = log("Signed in", source="admin", audience="signed_in")
        self.public = log("Signed out", source="admin", audience="signed_out")
        self.to_phone = log("To phone", device=self.phone, source="partner_token", site="portal.example.com")
        log("Chat", account=self.ravi, source="chat")
        log("Code", account=self.ravi, source="verification")
        log("Test", source="test")
        old = log("Before sign-up", source="admin", audience="everyone")
        AppNotificationLog.objects.filter(pk=old.pk).update(sent_at=self.ravi.created_at - timedelta(days=1))

    def _titles(self, auth, tab="new"):
        r = self.client.get(f"/app/v1/notifications?tab={tab}", **auth)
        self.assertEqual(r.status_code, 200, r.content)
        return [i["title"] for i in r.json()["items"]], r.json()["new_count"]

    def test_signed_in_list_and_read(self):
        titles, count = self._titles(self.bearer)
        self.assertEqual(set(titles), {"To Ravi", "Everyone", "Signed in"})
        self.assertEqual(count, 3)
        r = self.client.post("/app/v1/notifications/read", json.dumps({"ids": [self.direct.id]}),
                             content_type="application/json", **self.bearer)
        self.assertEqual(r.json()["new_count"], 2)
        self.assertEqual(self._titles(self.bearer, "read")[0], ["To Ravi"])
        r = self.client.post("/app/v1/notifications/read", json.dumps({"all": True}),
                             content_type="application/json", **self.bearer)
        self.assertEqual(r.json()["new_count"], 0)
        self.assertEqual(self.client.get("/app/v1/sync", **self.bearer).json()["notifications_new"], 0)

    def test_signed_out_phone_list(self):
        titles, count = self._titles(self.phone_auth)
        self.assertEqual(set(titles), {"Everyone", "Signed out", "To phone"})
        item = self.client.get("/app/v1/notifications", **self.phone_auth).json()["items"]
        self.assertEqual({i["kind"] for i in item if i["title"] == "To phone"}, {"site"})
        self.client.post("/app/v1/notifications/read", json.dumps({"ids": [self.everyone.id]}),
                         content_type="application/json", **self.phone_auth)
        self.assertEqual(self.client.get("/app/v1/notifications/count", **self.phone_auth).json()["new_count"], 2)
        # Reading on the phone doesn't touch the signed-in user's state.
        self.assertEqual(AppNotificationRead.objects.filter(account=self.ravi).count(), 0)

    def test_bad_phone_secret_is_refused(self):
        r = self.client.get("/app/v1/notifications", HTTP_X_DEVICE_ID="phone-x", HTTP_X_DEVICE_SECRET="nope")
        self.assertEqual(r.status_code, 401)

    def test_push_carries_its_log_id(self):
        sent = {}

        def fake_send(tokens, title, body, data=None, android=None, **kw):
            sent["data"], sent["android"] = data, android
            return len(tokens), 0

        with mock.patch.object(fcm, "is_configured", lambda: True), mock.patch.object(fcm, "send", fake_send):
            result = fcm.push(["tok"], "T", "B", source="admin", account=self.ravi, data={"link_url": "https://a.b"})
            self.assertEqual(sent["data"]["log_id"], result.log.pk)
            # Shown under the log's tag, so the app can clear it from the panel once read.
            self.assertEqual(sent["android"]["notification"]["tag"], f"syncup-log-{result.log.pk}")
            self.assertEqual(result.log.data, {"link_url": "https://a.b"}, "the log keeps the original data")
            self.assertEqual(result.log.status, "sent")
            fcm.push(["tok"], "Chat", "B", source="chat", account=self.ravi)
            self.assertIsNone(sent["android"], "chat isn't listed, so it isn't tagged")


class AdminPagesTests(TestCase):
    """The admin pages that gained the "Give SyncUp token" switch / Read column still render."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        self.client.force_login(get_user_model().objects.create_superuser("admin", "a@example.com", "pw"))
        ravi = _account("Ravi", "ravi@example.com")
        self.link = AppLink.objects.create(account=ravi, title="Mine", url="https://mine.example.net/", give_token=True)
        self.general = AppLink.objects.create(title="General", url="https://general.example.org/", give_token=True)
        cat = ShortcutCategory.objects.create(name="Health")
        self.shortcut = HomeShortcut.objects.create(category=cat, title="Portal", url="https://portal.example.com/",
                                                    give_token=True)
        from . import site_tokens
        site_tokens.tokens_for(account=ravi)  # issue tokens so "Reset tokens" shows
        AppNotificationLog.objects.create(title="Hello", body="b", source="admin", audience="everyone")

    def test_pages_render(self):
        from django.urls import reverse
        for name, args, needle in [
            ("mobile_link_edit", [self.link.id], "Give SyncUp token"),
            ("mobile_general_link_edit", [self.general.id], "Reset tokens"),
            ("mobile_home_shortcut_edit", [self.shortcut.id], "Give SyncUp token"),
            ("mobile_home_shortcuts", [], "Token"),
            ("mobile_push_log", [], ">Read<"),
        ]:
            r = self.client.get(reverse(name, args=args))
            self.assertEqual(r.status_code, 200, name)
            self.assertContains(r, needle, msg_prefix=name)

    def test_reset_button(self):
        from django.urls import reverse
        r = self.client.post(reverse("mobile_site_tokens_reset"),
                             {"host": "portal.example.com", "next": reverse("mobile_home_shortcuts")})
        self.assertRedirects(r, reverse("mobile_home_shortcuts"), fetch_redirect_response=False)
        from .models import AppSiteToken
        self.assertFalse(AppSiteToken.objects.filter(host="portal.example.com").exists())
