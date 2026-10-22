"""Tools → Shopping through the account sync: lists (`shoplist`) and items (`shopitem`) — accepted,
returned, capped, and they ping the account's other phones with "shopping".
Run: python manage.py test mobileapi.tests_shopping_sync"""
import json
from unittest import mock

from django.test import TestCase

from . import browser_sync, fcm
from .models import AppAccount, AppAuthToken, AppConfig, AppDevice, SyncItem

NOW = 1_760_000_000_000


class ShoppingSyncTests(TestCase):
    def setUp(self):
        AppConfig.load()
        self.ravi = AppAccount(name="Ravi", email="ravi@example.com")
        self.ravi.set_password("secret123")
        self.ravi.save()
        self.bearer = {"HTTP_AUTHORIZATION": f"Bearer {AppAuthToken.issue(self.ravi).key}"}
        AppDevice.objects.create(account=self.ravi, device_id="phone-b", fcm_token="tok-b")

    def sync(self, changes, kinds, device="phone-a", since=""):
        body = {"device_id": device, "since": since, "changes": changes, "kinds": kinds}
        with mock.patch.object(fcm, "is_configured", lambda: False):
            r = self.client.post("/app/v1/browser/sync", json.dumps(body), content_type="application/json", **self.bearer)
        self.assertEqual(r.status_code, 200, r.content[:300])
        return r.json()

    def test_shopping_kinds_are_known(self):
        self.assertTrue({"shoplist", "shopitem"} <= browser_sync.CLIENT_KINDS)
        self.assertEqual(browser_sync.MAX_LIVE["shoplist"], 200)
        self.assertEqual(browser_sync.MAX_LIVE["shopitem"], 5000)

    def test_lists_and_items_round_trip(self):
        kinds = ["shoplist", "shopitem"]
        self.sync([
            {"kind": "shoplist", "key": "groceries", "data": {"name": "Groceries", "color": "GREEN", "order": 0}, "updated_ms": NOW},
            {"kind": "shopitem", "key": "i1", "data": {"list": "groceries", "name": "Tomatoes", "qty": 1, "unit": "kg",
                                                       "aisle": "produce", "bought": False}, "updated_ms": NOW},
        ], kinds)
        back = self.sync([], kinds, device="phone-b")["changes"]
        self.assertEqual(sorted((c["kind"], c["key"]) for c in back), [("shopitem", "i1"), ("shoplist", "groceries")])
        item = next(c for c in back if c["kind"] == "shopitem")
        self.assertEqual(item["data"]["name"], "Tomatoes")
        self.assertEqual(item["data"]["unit"], "kg")

    def test_only_the_asked_kinds_come_back(self):
        self.sync([{"kind": "shopitem", "key": "i1", "data": {"name": "Milk"}, "updated_ms": NOW},
                   {"kind": "task", "key": "t1", "data": {"title": "Call"}, "updated_ms": NOW}], ["shopitem", "task"])
        self.assertEqual([c["kind"] for c in self.sync([], ["shoplist", "shopitem"], device="phone-b")["changes"]], ["shopitem"])
        self.assertEqual([c["kind"] for c in self.sync([], ["task", "tasklist"], device="phone-b")["changes"]], ["task"])

    def test_latest_change_wins_and_deletions_travel(self):
        kinds = ["shopitem"]
        first = self.sync([{"kind": "shopitem", "key": "i1", "data": {"name": "Milk", "bought": False}, "updated_ms": NOW}], kinds)
        # An older change from another phone loses.
        self.sync([{"kind": "shopitem", "key": "i1", "data": {"name": "Old milk"}, "updated_ms": NOW - 5}], kinds, device="phone-b")
        self.assertEqual(SyncItem.objects.get(kind="shopitem", key="i1").data["name"], "Milk")
        # Cleared (bought, then Clear): a tombstone reaches the other phone.
        self.sync([{"kind": "shopitem", "key": "i1", "data": None, "updated_ms": NOW + 5, "deleted": True}], kinds)
        back = self.sync([], kinds, device="phone-b", since=first["cursor"])["changes"]
        self.assertEqual([(c["key"], c["deleted"]) for c in back], [("i1", True)])

    def test_list_cap(self):
        with mock.patch.dict(browser_sync.MAX_LIVE, {"shoplist": 2}):
            resp = self.sync([{"kind": "shoplist", "key": f"l{i}", "data": {"name": f"L{i}"}, "updated_ms": NOW} for i in range(3)], ["shoplist"])
        self.assertEqual(SyncItem.objects.filter(kind="shoplist", deleted=False).count(), 2)
        self.assertEqual([r["key"] for r in resp["rejected"]], ["l2"])

    def test_shopping_changes_ping_other_phones(self):
        body = {"device_id": "phone-a", "since": "", "kinds": ["shopitem"],
                "changes": [{"kind": "shopitem", "key": "i1", "data": {"name": "Bread"}, "updated_ms": NOW}]}
        with mock.patch.object(fcm, "is_configured", lambda: True), mock.patch.object(fcm, "send_silent") as ping:
            r = self.client.post("/app/v1/browser/sync", json.dumps(body), content_type="application/json", **self.bearer)
        self.assertEqual(r.status_code, 200)
        ping.assert_called_once()
        self.assertEqual(ping.call_args.args[0], ["tok-b"])
        self.assertEqual(ping.call_args.args[1], {"type": "sync", "what": "shopping"})

    def test_all_sync_switches_reach_the_admin_view(self):
        types = ["bookmarks", "history", "tabs", "settings", "calendar", "water", "steps", "habits", "sleep", "mood", "shopping"]
        body = {"device_id": "phone-a", "since": "", "changes": [], "kinds": ["shoplist"],
                "tabs": {"device_name": "Pixel", "tabs": [], "state": {"enabled": True, "types": types}}}
        r = self.client.post("/app/v1/browser/sync", json.dumps(body), content_type="application/json", **self.bearer)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(SyncItem.objects.get(kind="tabs", key="phone-a").data["state"]["types"], types)
