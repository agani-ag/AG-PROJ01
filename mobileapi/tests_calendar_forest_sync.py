"""Calendar & Forest items in the browser sync (browser_sync.py): new kinds, per-feature `kinds`
filter, live-item caps and the silent "sync" ping.  Run: python manage.py test mobileapi.tests_calendar_forest_sync"""
import json
from unittest import mock

from django.test import TestCase

from . import browser_sync, fcm
from .models import AppAccount, AppAuthToken, AppConfig, AppDevice, SyncItem


def _account(name, email):
    a = AppAccount(name=name, email=email)
    a.set_password("secret123")
    a.save()
    return a


class CalendarForestSyncTests(TestCase):
    def setUp(self):
        AppConfig.load()
        self.ravi = _account("Ravi", "ravi@example.com")
        self.bearer = {"HTTP_AUTHORIZATION": f"Bearer {AppAuthToken.issue(self.ravi).key}"}
        AppDevice.objects.create(account=self.ravi, device_id="phone-a", fcm_token="tok-a")
        AppDevice.objects.create(account=self.ravi, device_id="phone-b", fcm_token="tok-b")

    def _sync(self, device, changes=(), since="", kinds=None):
        body = {"device_id": device, "since": since, "changes": list(changes)}
        if kinds is not None:
            body["kinds"] = kinds
        r = self.client.post("/app/v1/browser/sync", json.dumps(body), content_type="application/json", **self.bearer)
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    @staticmethod
    def _ch(kind, key, ms, data=None, deleted=False):
        return {"kind": kind, "key": key, "data": data or {}, "updated_ms": ms, "deleted": deleted}

    @mock.patch.object(fcm, "is_configured", lambda: True)
    @mock.patch.object(fcm, "send_silent")
    def test_tasks_go_round_and_ping_the_other_phone(self, send_silent):
        self._sync("phone-a", [self._ch("task", "t1", 1000, {"title": "Pay bill"}), self._ch("tasklist", "l1", 1000, {"name": "Home"})],
                   kinds=["task", "tasklist"])
        send_silent.assert_called_once()
        tokens, data = send_silent.call_args[0]
        self.assertEqual(tokens, ["tok-b"], "only the other phone")
        self.assertEqual(data, {"type": "sync", "what": "calendar"})
        got = self._sync("phone-b", kinds=["task", "tasklist"])
        self.assertEqual({(c["kind"], c["key"]) for c in got["changes"]}, {("task", "t1"), ("tasklist", "l1")})
        self.assertEqual(got["changes"][0]["data"]["title"] if got["changes"][0]["kind"] == "task" else got["changes"][1]["data"]["title"], "Pay bill")

    @mock.patch.object(fcm, "is_configured", lambda: True)
    @mock.patch.object(fcm, "send_silent")
    def test_forest_and_browser_kinds_do_not_ping(self, send_silent):
        self._sync("phone-a", [self._ch("tree", "tr1", 1000, {"species": "NEEM"}), self._ch("calmlog", "20261014|phone-a", 1000, {"min": 20}),
                               self._ch("bookmark", "b1", 1000, {"url": "https://x.example"})])
        send_silent.assert_not_called()

    @mock.patch.object(fcm, "is_configured", lambda: False)
    def test_kinds_filter_keeps_features_apart(self):
        self._sync("phone-a", [self._ch("task", "t1", 1000), self._ch("tree", "tr1", 1000), self._ch("bookmark", "b1", 1000, {"url": "u"})])
        cal = self._sync("phone-b", kinds=["task", "tasklist"])
        self.assertEqual([c["key"] for c in cal["changes"]], ["t1"])
        forest = self._sync("phone-b", kinds=["tree", "calmlog"])
        self.assertEqual([c["key"] for c in forest["changes"]], ["tr1"])
        everything = self._sync("phone-b")  # older apps: no filter
        self.assertEqual({c["key"] for c in everything["changes"]}, {"t1", "tr1", "b1"})
        junk = self._sync("phone-b", kinds=["tabs", "nope"])  # unknown kinds → nothing
        self.assertEqual(junk["changes"], [])

    @mock.patch.object(fcm, "is_configured", lambda: False)
    def test_latest_change_wins_and_tombstones_travel(self):
        self._sync("phone-a", [self._ch("task", "t1", 2000, {"title": "new"})])
        self._sync("phone-b", [self._ch("task", "t1", 1000, {"title": "old"})])  # older: ignored
        self.assertEqual(SyncItem.objects.get(account=self.ravi, kind="task", key="t1").data["title"], "new")
        first = self._sync("phone-b", kinds=["task"])
        self._sync("phone-a", [self._ch("task", "t1", 3000, deleted=True)])
        later = self._sync("phone-b", since=first["cursor"], kinds=["task"])
        self.assertEqual([(c["key"], c["deleted"]) for c in later["changes"]], [("t1", True)])

    @mock.patch.object(fcm, "is_configured", lambda: False)
    def test_live_cap(self):
        with mock.patch.dict(browser_sync.MAX_LIVE, {"task": 2}):
            r = self._sync("phone-a", [self._ch("task", f"t{i}", 1000) for i in range(3)])
            self.assertEqual([x["key"] for x in r["rejected"]], ["t2"])
            self._sync("phone-a", [self._ch("task", "t0", 2000, deleted=True)])
            r = self._sync("phone-a", [self._ch("task", "t9", 2000)])
            self.assertEqual(r["rejected"], [], "room again after a delete")
