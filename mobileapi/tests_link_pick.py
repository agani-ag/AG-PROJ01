"""'Pick from existing URLs' on the link pages: picking a URL also brings the title, description and User-Agent
append a link at that URL already has. Run: python manage.py test mobileapi.tests_link_pick"""
from django.contrib.auth import get_user_model
from django.test import TestCase

from .admin_views import _distinct_link_urls
from .models import AppAccount, AppLink


class PickExistingUrlTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("root", password="x"))
        self.asha = AppAccount.objects.create(name="Asha", email="a@example.org")
        self.ravi = AppAccount.objects.create(name="Ravi", email="r@example.org")

    def picks(self):
        return {p["url"]: p for p in _distinct_link_urls()}

    def test_a_url_carries_its_links_title_description_and_user_agent(self):
        AppLink.objects.create(account=self.asha, title="GST SYNC (Admin)", url="https://gst.example/m/",
                               description="Admin Mode", ua_append="/AG.Sync v2.1")
        p = self.picks()["https://gst.example/m/"]
        self.assertEqual((p["title"], p["description"], p["ua_append"]), ("GST SYNC (Admin)", "Admin Mode", "/AG.Sync v2.1"))
        self.assertEqual(p["label"], "GST SYNC (Admin) — https://gst.example/m/")

    def test_one_entry_per_url_from_the_newest_link_with_blanks_filled_from_older_ones(self):
        AppLink.objects.create(account=self.asha, title="Old name", url="https://gst.example/", description="Kept",
                               ua_append="/AG.Sync v2.0")
        AppLink.objects.create(account=self.ravi, title="New name", url="https://gst.example/", description="", ua_append="")
        picks = [p for p in _distinct_link_urls() if p["url"] == "https://gst.example/"]
        self.assertEqual(len(picks), 1)
        self.assertEqual((picks[0]["title"], picks[0]["description"], picks[0]["ua_append"]), ("New name", "Kept", "/AG.Sync v2.0"))

    def test_nothing_to_carry_stays_blank(self):
        AppLink.objects.create(account=self.asha, title="Plain", url="https://plain.example/", description=None)
        p = self.picks()["https://plain.example/"]
        self.assertEqual((p["description"], p["ua_append"]), ("", ""))

    def test_general_links_are_on_the_list_too_in_title_order(self):
        AppLink.objects.create(title="Zeta", url="https://z.example/")
        AppLink.objects.create(account=self.asha, title="alpha", url="https://a.example/")
        self.assertEqual([p["title"] for p in _distinct_link_urls()], ["alpha", "Zeta"])

    def test_both_link_pages_put_them_on_the_options_and_fill_the_fields(self):
        AppLink.objects.create(account=self.ravi, title="GST", url="https://gst.example/", description="Admin Mode",
                               ua_append="/AG.Sync v2.1")
        for page in (f"/mobile/links/add?account={self.asha.id}", "/mobile/general-links/add"):
            r = self.client.get(page)
            self.assertContains(r, 'data-title="GST" data-description="Admin Mode" data-ua="/AG.Sync v2.1"', msg_prefix=page)
            self.assertContains(r, 'data-for-ua="id_ua_append"', msg_prefix=page)
            self.assertContains(r, 'data-for-title="id_title"', msg_prefix=page)

    def test_the_value_is_escaped_in_the_page(self):
        AppLink.objects.create(account=self.ravi, title='Say "hi" <b>', url="https://q.example/", ua_append="/AG.Sync v2.1")
        r = self.client.get(f"/mobile/links/add?account={self.asha.id}")
        self.assertContains(r, 'data-title="Say &quot;hi&quot; &lt;b&gt;"')
