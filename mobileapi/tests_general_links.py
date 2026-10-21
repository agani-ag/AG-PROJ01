"""General links for everyone — signed in or not (GET /app/v1/general-links, the "general" flag).
Run: python manage.py test mobileapi.tests_general_links"""
from unittest import mock

from django.test import TestCase

from . import fcm
from .models import AppAccount, AppAuthToken, AppConfig, AppLink


@mock.patch.object(fcm, "is_configured", lambda: False)  # never reach Firebase from tests
class GeneralLinksTests(TestCase):
    def setUp(self):
        AppConfig.load()
        self.ravi = AppAccount(name="Ravi", email="ravi@example.com")
        self.ravi.set_password("secret123")
        self.ravi.save()
        AppLink.objects.create(title="Income Tax", url="https://www.incometax.gov.in/", give_token=True)
        AppLink.objects.create(title="GST Portal", url="https://www.gst.gov.in/")
        AppLink.objects.create(title="Old", url="https://old.example.org/", is_active=False)
        AppLink.objects.create(account=self.ravi, title="HR Portal", url="https://hr.example.com/")

    def test_signed_out_get_the_active_general_links_only(self):
        r = self.client.get("/app/v1/general-links")
        self.assertEqual(r.status_code, 200)
        links = r.json()["links"]
        self.assertEqual([link["title"] for link in links], ["GST Portal", "Income Tax"])
        self.assertTrue(all(link["general"] for link in links))
        self.assertTrue(all(link["notify_token"] == "" for link in links), "tokens come from device/hello")
        self.assertTrue(links[1]["give_token"])

    def test_same_version_answers_304(self):
        first = self.client.get("/app/v1/general-links")
        version = first.json()["version"]
        self.assertEqual(self.client.get(f"/app/v1/general-links?v={version}").status_code, 304)
        self.assertEqual(self.client.get("/app/v1/general-links", HTTP_IF_NONE_MATCH=first["ETag"]).status_code, 304)
        AppLink.objects.create(title="New one", url="https://new.example.org/")
        self.assertEqual(self.client.get(f"/app/v1/general-links?v={version}").status_code, 200)

    def test_signed_in_list_marks_general_links(self):
        bearer = {"HTTP_AUTHORIZATION": f"Bearer {AppAuthToken.issue(self.ravi).key}"}
        urls = self.client.get("/app/v1/sync", **bearer).json()["urls"]
        self.assertEqual([(u["title"], u["general"]) for u in urls],
                         [("HR Portal", False), ("GST Portal", True), ("Income Tax", True)])

    def test_admin_page_says_everyone(self):
        from django.contrib.auth import get_user_model

        self.client.force_login(get_user_model().objects.create_superuser("boss", "boss@example.com", "pw-123456"))
        self.assertContains(self.client.get("/mobile/general-links"), "signed in or not")
