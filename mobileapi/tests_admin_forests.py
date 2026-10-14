"""Admin → Forests: the list, one forest, giving and removing trees, the tree drawings.
Run: python manage.py test mobileapi.tests_admin_forests"""
import json
import time
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from . import fcm
from . import forests as fx
from .models import AppAccount, AppAuthToken, AppConfig, AppDevice, SyncItem


def _account(name, email):
    a = AppAccount(name=name, email=email)
    a.set_password("secret123")
    a.save()
    return a


class AdminForestsTests(TestCase):
    def setUp(self):
        AppConfig.load()
        self.admin = get_user_model().objects.create_superuser("boss", "boss@example.com", "pw-123456")
        self.client.force_login(self.admin)
        self.ravi = _account("Ravi", "ravi@example.com")
        self.meena = _account("Meena", "meena@example.com")
        AppDevice.objects.create(account=self.ravi, device_id="phone-a", fcm_token="tok-a")
        now = int(time.time() * 1000)
        # Ravi's own forest, as his phone synced it: a grown mango, a neem sapling, 3 calm days.
        SyncItem.objects.create(account=self.ravi, kind="tree", key="t-mango", updated_ms=now, device_id="phone-a",
                                data=fx.new_tree_data("MANGO", "GROWN", 7, "Amma's mango", now))
        SyncItem.objects.create(account=self.ravi, kind="tree", key="t-neem", updated_ms=now, device_id="phone-a",
                                data=fx.new_tree_data("NEEM", "SAPLING", 7, "", now))
        for day in ("20261012", "20261013", "20261014"):
            SyncItem.objects.create(account=self.ravi, kind="calmlog", key=f"{day}|phone-a", updated_ms=now, data={"min": 25})

    def test_list_shows_forests_and_finds_everyone(self):
        r = self.client.get(reverse("mobile_forests"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Ravi")
        self.assertNotContains(r, "Meena")  # no forest yet
        self.assertContains(r, "1 h 15 m")  # 3 x 25 calm minutes
        r = self.client.get(reverse("mobile_forests") + "?show=all&q=meen")
        self.assertContains(r, "Meena")
        self.assertContains(r, "No trees yet")

    def test_forest_page_draws_the_scene_trees_and_week(self):
        for sky in ("", "?sky=day", "?sky=night"):
            r = self.client.get(reverse("mobile_forest", args=[self.ravi.pk]) + sky)
            self.assertEqual(r.status_code, 200)
            self.assertContains(r, 'class="forest-scene"')
            self.assertContains(r, 'data-tree="t-mango"')
            self.assertContains(r, "Amma&#x27;s mango")
            self.assertContains(r, "Calm time")
        r = self.client.get(reverse("mobile_forest", args=[self.meena.pk]))
        self.assertContains(r, "No trees yet")

    @mock.patch.object(fcm, "is_configured", lambda: True)
    @mock.patch.object(fcm, "send_silent")
    def test_give_trees_reach_the_phone(self, send_silent):
        r = self.client.post(reverse("mobile_forest_add", args=[self.ravi.pk]),
                             {"species": "GULMOHAR", "stage": "YOUNG", "grow_days": "14", "count": "2", "name": "Gift"})
        self.assertRedirects(r, reverse("mobile_forest", args=[self.ravi.pk]), fetch_redirect_response=False)
        given = SyncItem.objects.filter(account=self.ravi, kind="tree", device_id=fx.ADMIN_DEVICE, deleted=False)
        self.assertEqual(given.count(), 2)
        now = int(time.time() * 1000)
        for item in given:
            t = fx.Tree.from_item(item)
            self.assertEqual(t.stage(now), "YOUNG")
            self.assertEqual(t.seeds_given, 1, "a gift never uses the user's own seeds")
        self.assertEqual(sorted(fx.Tree.from_item(i).name for i in given), ["Gift 1", "Gift 2"])
        send_silent.assert_called_once()
        self.assertEqual(send_silent.call_args[0], (["tok-a"], {"type": "sync", "what": "forest"}))
        # The phone's next sync brings them.
        bearer = {"HTTP_AUTHORIZATION": f"Bearer {AppAuthToken.issue(self.ravi).key}"}
        body = {"device_id": "phone-a", "since": "", "changes": [], "kinds": ["tree", "calmlog"]}
        got = self.client.post("/app/v1/browser/sync", json.dumps(body), content_type="application/json", **bearer).json()
        self.assertEqual(sum(1 for c in got["changes"] if c["kind"] == "tree"), 4)

    def test_every_stage_is_what_was_asked(self):
        now = int(time.time() * 1000)
        for stage in fx.STAGES:
            for days in (1, 3, 7, 14):
                t = fx.Tree.from_item(SyncItem(key="x", data=fx.new_tree_data("NEEM", stage, days, "", now), updated_ms=now))
                self.assertEqual(t.stage(now), stage, f"{stage} over {days} days")

    def test_bad_gift_is_refused(self):
        url = reverse("mobile_forest_add", args=[self.meena.pk])
        self.client.post(url, {"species": "OAK", "stage": "GROWN", "grow_days": "7", "count": "1"})
        self.client.post(url, {"species": "NEEM", "stage": "GROWN", "grow_days": "30", "count": "1"})
        self.client.post(url, {"species": "NEEM", "stage": "GROWN", "grow_days": "7", "count": "99"})
        self.assertFalse(SyncItem.objects.filter(account=self.meena).exists())

    def test_remove_leaves_a_tombstone(self):
        r = self.client.post(reverse("mobile_forest_remove", args=[self.ravi.pk, "t-neem"]))
        self.assertEqual(r.status_code, 302)
        item = SyncItem.objects.get(account=self.ravi, kind="tree", key="t-neem")
        self.assertTrue(item.deleted)
        self.assertEqual(item.data, {})

    def test_tree_drawings(self):
        r = self.client.get(reverse("mobile_forest_art", args=["MANGO-GROWN-123-5-0"]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "image/svg+xml")
        self.assertIn(b"<svg", r.content)
        r = self.client.get(reverse("mobile_forest_art", args=["NEEM-SEED-5-1-0-c"]))
        self.assertIn(b'viewBox="40 168 120 66"', r.content)
        self.assertEqual(self.client.get(reverse("mobile_forest_art", args=["OAK-GROWN-1-1-0"])).status_code, 404)
        self.assertEqual(self.client.get(reverse("mobile_forest_pond")).status_code, 200)
        for species in fx.SPECIES:
            for stage in fx.STAGES:
                for month in (1, 5, 9):
                    self.assertEqual(self.client.get(reverse("mobile_forest_art", args=[f"{species}-{stage}-42-{month}-1"])).status_code, 200)

    def test_only_superusers(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("mobile_forests")).status_code, 302)
        self.assertEqual(self.client.post(reverse("mobile_forest_add", args=[self.ravi.pk]), {"species": "NEEM"}).status_code, 302)
        self.assertEqual(SyncItem.objects.filter(account=self.ravi, kind="tree").count(), 2)
