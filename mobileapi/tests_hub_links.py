"""SyncHub → SyncUp: the open hub/links API (links key), link icons + colours, and the admin side.
Run: python manage.py test mobileapi.tests_hub_links"""
import json

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from . import hub_links
from .models import AppConfig, AppLink

KEY = "test-links-key-123"


class HubLinksApiTests(TestCase):
    def setUp(self):
        cache.clear()
        cfg = AppConfig.load()
        cfg.links_key = KEY
        cfg.save()

    def post(self, **body):
        body.setdefault("key", KEY)
        return self.client.post("/app/v1/hub/links", json.dumps(body), content_type="application/json")

    def hibiscus(self, **extra):
        return self.post(ref="synchub:hibiscus", title="Hibiscus", url="https://hub.example.com/hibiscus/",
                         description="Cycle diary", icon="flower", color="rose", give_token=True, **extra)

    def test_add_then_update_by_ref(self):
        r = self.hibiscus()
        self.assertEqual(r.status_code, 201, r.content)
        self.assertTrue(r.json()["created"])
        link = AppLink.objects.get(external_id="synchub:hibiscus")
        self.assertIsNone(link.account)
        self.assertEqual((link.icon, link.color, link.give_token, link.is_active), ("flower", "rose", True, True))
        r = self.post(ref="synchub:hibiscus", title="Hibiscus diary")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["created"])
        link.refresh_from_db()
        self.assertEqual(link.title, "Hibiscus diary")
        self.assertEqual(link.url, "https://hub.example.com/hibiscus/", "fields not sent stay as they were")
        self.assertEqual(AppLink.objects.filter(external_id="synchub:hibiscus").count(), 1)

    def test_everyone_gets_it_with_its_icon(self):
        self.hibiscus()
        links = self.client.get("/app/v1/general-links").json()["links"]
        self.assertEqual([(l["title"], l["icon"], l["color"], l["general"]) for l in links], [("Hibiscus", "flower", "rose", True)])

    def test_hide_and_show(self):
        self.hibiscus()
        self.post(ref="synchub:hibiscus", active=False)
        self.assertFalse(AppLink.objects.get(external_id="synchub:hibiscus").is_active)
        self.post(ref="synchub:hibiscus", active=True)
        self.assertTrue(AppLink.objects.get(external_id="synchub:hibiscus").is_active)

    def test_admin_off_wins(self):
        self.hibiscus()
        link = AppLink.objects.get(external_id="synchub:hibiscus")
        link.admin_off, link.is_active = True, False
        link.save()
        r = self.post(ref="synchub:hibiscus", title="New name", active=True)
        link.refresh_from_db()
        self.assertEqual(link.title, "New name", "SyncHub can still change it")
        self.assertFalse(link.is_active, "but not turn it back on")
        self.assertTrue(r.json()["link"]["admin_off"])

    def test_key_is_required(self):
        self.assertEqual(self.hibiscus(key="wrong").status_code, 403)
        self.assertEqual(self.client.get("/app/v1/hub/links").status_code, 403)
        cfg = AppConfig.load()
        cfg.links_key = ""
        cfg.save()
        self.assertEqual(self.hibiscus().status_code, 503)
        self.assertFalse(AppLink.objects.exists())

    def test_bad_fields(self):
        self.assertEqual(self.post(ref="x", title="A", url="http://plain.example.com/").status_code, 400)
        self.assertEqual(self.post(ref="x", title="A", url="https://ok.example.com/", icon="dragon").status_code, 400)
        self.assertEqual(self.post(ref="x", title="A", url="https://ok.example.com/", color="pink").status_code, 400)
        self.assertEqual(self.post(ref="has space", title="A", url="https://ok.example.com/").status_code, 400)
        self.assertEqual(self.post(ref="x", url="https://ok.example.com/").status_code, 400, "a new link needs a title")
        self.assertFalse(AppLink.objects.exists())

    def test_only_its_own_links(self):
        admin_link = AppLink.objects.create(title="GST Portal", url="https://www.gst.gov.in/")
        self.hibiscus()
        listed = self.client.get(f"/app/v1/hub/links?key={KEY}").json()["links"]
        self.assertEqual([l["ref"] for l in listed], ["synchub:hibiscus"])
        admin_link.refresh_from_db()
        self.assertEqual(admin_link.title, "GST Portal")

    def test_limit(self):
        for i in range(hub_links.MAX_LINKS):
            AppLink.objects.create(title=f"T{i}", url="https://x.example.com/", external_id=f"synchub:t{i}")
        self.assertEqual(self.hibiscus().status_code, 400)
        self.assertEqual(self.post(ref="synchub:t1", title="Still updatable").status_code, 200)


class LinkIconAdminTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("boss", "boss@example.com", "pw-123456"))

    def test_pages_render_with_the_picker(self):
        AppLink.objects.create(title="Hibiscus", url="https://hub.example.com/hibiscus/", icon="flower", color="rose",
                               external_id="synchub:hibiscus")
        link = AppLink.objects.create(title="GST Portal", url="https://www.gst.gov.in/")
        r = self.client.get("/mobile/general-links")
        self.assertContains(r, "SyncHub")
        self.assertContains(r, "Make a key")
        self.assertContains(self.client.get(f"/mobile/general-links/{link.id}/edit"), 'name="ip_mode"')
        self.assertContains(self.client.get("/mobile/general-links/add"), 'value="flower"')

    def test_save_icon_and_colour(self):
        link = AppLink.objects.create(title="GST Portal", url="https://www.gst.gov.in/")
        r = self.client.post(f"/mobile/general-links/{link.id}/edit", {
            "title": "GST Portal", "url": "https://www.gst.gov.in/", "icon": "gov", "color": "amber", "is_active": "on",
        })
        self.assertEqual(r.status_code, 302)
        link.refresh_from_db()
        self.assertEqual((link.icon, link.color), ("gov", "amber"))

    def test_admin_switch_off_on_a_synchub_link(self):
        link = AppLink.objects.create(title="J6", url="https://hub.example.com/j6/", external_id="synchub:j6")
        self.client.post(f"/mobile/general-links/{link.id}/edit", {"title": "J6", "url": "https://hub.example.com/j6/"})
        link.refresh_from_db()
        self.assertTrue(link.admin_off)
        self.assertFalse(link.is_active)

    def test_new_key(self):
        self.client.post("/mobile/general-links/key")
        first = AppConfig.load().links_key
        self.assertTrue(len(first) > 30)
        self.client.post("/mobile/general-links/key")
        self.assertNotEqual(AppConfig.load().links_key, first)


class PartnerLinkColourTests(TestCase):
    def test_partner_link_keeps_known_icon_and_colour_only(self):
        from .partner_api import _color, _icon

        self.assertEqual((_icon("Flower"), _color("ROSE")), ("flower", "rose"))
        self.assertEqual((_icon("dragon"), _color("pink")), (None, ""))
