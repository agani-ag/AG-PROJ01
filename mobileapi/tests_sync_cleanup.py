"""The cleanup cron tidies the synced items without phones noticing: history repeats, stale open-tab rows,
retired kinds. Deleted markers are kept. The sync endpoint is called as the app calls it, before and after.
Run: python manage.py test mobileapi.tests_sync_cleanup"""
import json
import time
from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from . import fcm
from .cleanup import run_cleanup, tidy_sync_items
from .models import AppAccount, AppAuthToken, AppConfig, AppDevice, SyncItem

NOW_MS = int(time.time() * 1000)
DAY_MS = 86400000


def account(name):
    a = AppAccount.objects.create(name=name, email=f"{name.lower()}@example.org")
    AppDevice.objects.create(account=a, device_id=f"{name}-phone", fcm_token="")
    return a


def visit(acc, key, url, days_ago, title="Page"):
    ms = NOW_MS - int(days_ago * DAY_MS)
    return SyncItem.objects.create(account=acc, kind="history", key=key, updated_ms=ms,
                                   data={"url": url, "title": title, "visited_ms": ms}, device_id=f"{acc.name}-phone")


def keys(acc, kind="history", **filters):
    return sorted(SyncItem.objects.filter(account=acc, kind=kind, **filters).values_list("key", flat=True))


@mock.patch.object(fcm, "is_configured", lambda: False)
class HistoryTests(TestCase):
    def setUp(self):
        AppConfig.load()
        self.ravi, self.priya = account("Ravi"), account("Priya")

    def test_older_repeats_go_and_the_newest_visit_always_stays(self):
        visit(self.ravi, "v1", "https://a.example/", 60)
        visit(self.ravi, "v2", "https://a.example/", 45)
        visit(self.ravi, "v3", "https://a.example/", 50)
        visit(self.ravi, "q1", "https://b.example/", 70)  # only visit: stays however old
        self.assertEqual(tidy_sync_items(timezone.now())["sync_history_repeats"], 2)
        self.assertEqual(keys(self.ravi), ["q1", "v2"])

    def test_every_visit_of_the_last_30_days_stays_for_most_used_sites(self):
        for i, days in enumerate((29, 20, 10, 3, 1)):
            visit(self.ravi, f"recent{i}", "https://a.example/", days)
        visit(self.ravi, "old1", "https://a.example/", 40)
        visit(self.ravi, "old2", "https://a.example/", 80)
        self.assertEqual(tidy_sync_items(timezone.now())["sync_history_repeats"], 2)
        self.assertEqual(keys(self.ravi), [f"recent{i}" for i in range(5)])

    def test_each_account_keeps_its_own(self):
        visit(self.ravi, "r1", "https://a.example/", 40)
        visit(self.priya, "p1", "https://a.example/", 35)
        tidy_sync_items(timezone.now())
        self.assertEqual((keys(self.ravi), keys(self.priya)), (["r1"], ["p1"]))

    def test_the_same_address_only_counts_as_the_same_page(self):
        visit(self.ravi, "a", "https://a.example/", 40)
        visit(self.ravi, "b", "https://a.example/?q=1", 35)
        visit(self.ravi, "c", "https://A.example/", 35)
        tidy_sync_items(timezone.now())
        self.assertEqual(keys(self.ravi), ["a", "b", "c"])

    def test_deleted_markers_and_rows_without_an_address_are_left(self):
        visit(self.ravi, "v1", "https://a.example/", 2)
        SyncItem.objects.create(account=self.ravi, kind="history", key="gone", deleted=True, updated_ms=NOW_MS)
        SyncItem.objects.create(account=self.ravi, kind="history", key="odd", data={"title": "no url"}, updated_ms=NOW_MS)
        tidy_sync_items(timezone.now())
        self.assertEqual(keys(self.ravi), ["gone", "odd", "v1"])

    def test_a_page_visited_more_than_a_batch_of_times(self):
        SyncItem.objects.bulk_create([
            SyncItem(account=self.ravi, kind="history", key=f"k{i:04}", updated_ms=NOW_MS - 31 * DAY_MS - i,
                     data={"url": "https://a.example/", "visited_ms": NOW_MS - 31 * DAY_MS - i}) for i in range(1201)
        ])
        self.assertEqual(tidy_sync_items(timezone.now())["sync_history_repeats"], 1200)
        self.assertEqual(keys(self.ravi), ["k0000"])

    def test_other_kinds_are_never_merged(self):
        for i in range(3):
            SyncItem.objects.create(account=self.ravi, kind="bookmark", key=f"b{i}", data={"url": "https://a.example/"}, updated_ms=NOW_MS)
        tidy_sync_items(timezone.now())
        self.assertEqual(len(keys(self.ravi, "bookmark")), 3)


@mock.patch.object(fcm, "is_configured", lambda: False)
class PhonesDontNoticeTests(TestCase):
    """The sync endpoint, called as the app calls it, around a cleanup."""

    def setUp(self):
        AppConfig.load()
        self.ravi = account("Ravi")
        AppDevice.objects.create(account=self.ravi, device_id="Ravi-tablet", fcm_token="")
        self.bearer = {"HTTP_AUTHORIZATION": f"Bearer {AppAuthToken.issue(self.ravi).key}"}

    def sync(self, device, since="", changes=()):
        body = {"device_id": device, "since": since, "changes": list(changes),
                "tabs": {"device_name": device, "tabs": [], "state": {"enabled": True, "types": ["history"]}}}
        r = self.client.post("/app/v1/browser/sync", json.dumps(body), content_type="application/json", **self.bearer)
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def visit_change(self, key, days_ago):
        ms = NOW_MS - days_ago * DAY_MS
        return {"kind": "history", "key": key, "updated_ms": ms, "deleted": False,
                "data": {"url": "https://a.example/", "title": "A", "visited_ms": ms}}

    def test_a_phone_that_has_the_visits_hears_nothing_and_a_new_phone_gets_the_recent_ones(self):
        visits = (("v1", 60), ("v2", 45), ("v3", 20), ("v4", 1))
        first = self.sync("Ravi-phone", changes=[self.visit_change(k, d) for k, d in visits])
        tablet = self.sync("Ravi-tablet")
        self.assertEqual(sorted(c["key"] for c in tablet["changes"] if c["kind"] == "history"), ["v1", "v2", "v3", "v4"])

        stats = run_cleanup()
        self.assertEqual(stats["sync_history_repeats"], 2)

        again = self.sync("Ravi-phone", since=first["cursor"])
        self.assertEqual([c for c in again["changes"] if c["kind"] == "history"], [])  # no deletions sent
        tablet_again = self.sync("Ravi-tablet", since=tablet["cursor"])
        self.assertEqual([c for c in tablet_again["changes"] if c["kind"] == "history"], [])

        new_phone = self.sync("Ravi-new")
        # every visit of the last 30 days, so its Most used sites can count them
        self.assertEqual(sorted((c["key"], c["deleted"]) for c in new_phone["changes"] if c["kind"] == "history"),
                         [("v3", False), ("v4", False)])

    def test_a_phone_still_sending_an_old_visit_is_fine(self):
        self.sync("Ravi-phone", changes=[self.visit_change("v1", 50), self.visit_change("v2", 1)])
        run_cleanup()
        r = self.sync("Ravi-phone", changes=[self.visit_change("v1", 50)])  # resent: comes back, goes at the next cleanup
        self.assertEqual(r["rejected"], [])
        self.assertEqual(run_cleanup()["sync_history_repeats"], 1)
        self.assertEqual(keys(self.ravi), ["v2"])

    def test_a_phone_deleting_a_visit_already_tidied_away_still_reaches_the_others(self):
        self.sync("Ravi-phone", changes=[self.visit_change("v1", 50), self.visit_change("v2", 1)])
        tablet = self.sync("Ravi-tablet")
        run_cleanup()
        self.sync("Ravi-phone", changes=[{"kind": "history", "key": "v1", "updated_ms": NOW_MS, "deleted": True, "data": {}}])
        later = self.sync("Ravi-tablet", since=tablet["cursor"])
        self.assertIn(("v1", True), [(c["key"], c["deleted"]) for c in later["changes"]])


@mock.patch.object(fcm, "is_configured", lambda: False)
class CursorTests(TestCase):
    """Any cursor works: the server's own, an old one, or one it can't read (a full download)."""

    def setUp(self):
        self.ravi = account("Ravi")
        self.bearer = {"HTTP_AUTHORIZATION": f"Bearer {AppAuthToken.issue(self.ravi).key}"}
        visit(self.ravi, "v1", "https://a.example/", 1)

    def sync(self, since):
        body = {"device_id": "Ravi-phone", "since": since, "changes": []}
        return self.client.post("/app/v1/browser/sync", json.dumps(body), content_type="application/json", **self.bearer)

    def test_unusable_cursors_give_a_full_download(self):
        for since in ("2026-13-45T99:00:00", "not a time", "0", 12345, ["x"]):
            with self.subTest(since=since):
                r = self.sync(since)
                self.assertEqual(r.status_code, 200)
                self.assertEqual([c["key"] for c in r.json()["changes"]], ["v1"])

    def test_an_old_cursor_still_works_after_a_cleanup(self):
        old = (timezone.now() - timedelta(days=400)).isoformat()
        run_cleanup()
        r = self.sync(old)
        self.assertEqual((r.status_code, [c["key"] for c in r.json()["changes"]]), (200, ["v1"]))


class TabsTests(TestCase):
    def setUp(self):
        self.ravi, self.priya = account("Ravi"), account("Priya")

    def tabs_row(self, acc, device_id, days_ago=0):
        row = SyncItem.objects.create(account=acc, kind="tabs", key=device_id, data={"device_name": device_id, "tabs": []})
        SyncItem.objects.filter(pk=row.pk).update(changed_at=timezone.now() - timedelta(days=days_ago))
        return row

    def test_only_tabs_of_phones_still_with_the_account_and_recently_synced_stay(self):
        AppDevice.objects.create(account=self.ravi, device_id="ravi-old", fcm_token="")
        AppDevice.objects.create(account=self.priya, device_id="handed-on", fcm_token="")   # now Priya's
        AppDevice.objects.create(account=None, device_id="signed-out", fcm_token="")
        self.tabs_row(self.ravi, "Ravi-phone", days_ago=2)      # stays
        self.tabs_row(self.ravi, "ravi-old", days_ago=31)       # not synced for a month
        self.tabs_row(self.ravi, "uninstalled")                 # phone no longer registered
        self.tabs_row(self.ravi, "handed-on")                   # signed in to someone else
        self.tabs_row(self.ravi, "signed-out")                  # signed out
        self.tabs_row(self.priya, "Priya-phone", days_ago=29)   # stays
        self.assertEqual(tidy_sync_items(timezone.now())["sync_stale_tabs"], 4)
        self.assertEqual(sorted(SyncItem.objects.filter(kind="tabs").values_list("key", flat=True)), ["Priya-phone", "Ravi-phone"])


class RetiredAndDeletedTests(TestCase):
    def setUp(self):
        self.ravi = account("Ravi")

    def row(self, kind, key, deleted=False, days_ago=0):
        r = SyncItem.objects.create(account=self.ravi, kind=kind, key=key, deleted=deleted, updated_ms=NOW_MS)
        SyncItem.objects.filter(pk=r.pk).update(changed_at=timezone.now() - timedelta(days=days_ago))

    def test_kinds_the_app_no_longer_syncs_go_and_every_current_kind_stays(self):
        from .browser_sync import CLIENT_KINDS
        self.row("tree", "t1")
        self.row("calmlog", "c1")
        for kind in CLIENT_KINDS:
            self.row(kind, f"{kind}-1")
        self.assertEqual(tidy_sync_items(timezone.now())["sync_retired_kinds"], 2)
        self.assertEqual(set(SyncItem.objects.values_list("kind", flat=True)), set(CLIENT_KINDS))

    def test_deleted_markers_are_kept_however_old(self):
        self.row("bookmark", "old", deleted=True, days_ago=900)
        self.row("mood", "recent", deleted=True, days_ago=1)
        self.row("bookmark", "live-and-old", days_ago=400)
        stats = tidy_sync_items(timezone.now())
        self.assertNotIn("sync_old_deleted", stats)
        self.assertEqual(sorted(SyncItem.objects.values_list("key", flat=True)), ["live-and-old", "old", "recent"])

    def test_a_phone_away_for_a_long_time_still_hears_of_a_deletion(self):
        self.row("bookmark", "gone", deleted=True, days_ago=200)
        cursor = (timezone.now() - timedelta(days=300)).isoformat()
        run_cleanup()
        AppDevice.objects.create(account=self.ravi, device_id="Ravi-old", fcm_token="")
        bearer = {"HTTP_AUTHORIZATION": f"Bearer {AppAuthToken.issue(self.ravi).key}"}
        body = {"device_id": "Ravi-old", "since": cursor, "changes": []}
        with mock.patch.object(fcm, "is_configured", lambda: False):
            r = self.client.post("/app/v1/browser/sync", json.dumps(body), content_type="application/json", **bearer).json()
        self.assertIn(("gone", True), [(c["key"], c["deleted"]) for c in r["changes"]])


@override_settings(CRON_KEY="k")
class CronReplyTests(TestCase):
    def test_the_cleanup_cron_does_it_and_says_how_many(self):
        ravi = account("Ravi")
        visit(ravi, "v1", "https://a.example/", 40)
        visit(ravi, "v2", "https://a.example/", 1)
        reply = self.client.get("/cron/cleanup", HTTP_X_CRON_KEY="k").json()
        self.assertEqual({k: reply[k] for k in reply if k.startswith("sync_")},
                         {"sync_history_repeats": 1, "sync_stale_tabs": 0, "sync_retired_kinds": 0})
