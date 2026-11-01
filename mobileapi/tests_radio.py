"""Live stations for everyone (GET /app/v1/radio/channels, no sign-in) and AudioSync's presence events.
Run: python manage.py test mobileapi.tests_radio"""
import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from .models import AppConfig, RadioChannel


class RadioChannelsTests(TestCase):
    def setUp(self):
        cfg = AppConfig.load()
        cfg.radio_enabled = True
        cfg.save()
        RadioChannel.objects.create(broadcaster_id="b1", session_id="s1", name="Morning Mix", stream_url="https://example.org/stream",
                                    is_live=True, last_heartbeat_at=timezone.now())

    def test_signed_out_phone_gets_the_live_stations(self):
        r = self.client.get("/app/v1/radio/channels")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["enabled"])
        self.assertEqual([c["name"] for c in r.json()["channels"]], ["Morning Mix"])

    def test_admin_off_means_no_stations_for_anyone(self):
        cfg = AppConfig.load()
        cfg.radio_enabled = False
        cfg.save()
        r = self.client.get("/app/v1/radio/channels")
        self.assertEqual(r.json(), {"enabled": False, "channels": []})


class StationPresenceTests(TestCase):
    """AudioSync's presence events: the address, and what a station offers."""
    NEW = "/station/presence"
    STREAM = "https://abc.trycloudflare.com/stream"
    FILES = "https://abc.trycloudflare.com/api/files/"
    INFO = "https://abc.trycloudflare.com/api/info"

    def setUp(self):
        cfg = AppConfig.load()
        cfg.radio_enabled = True
        cfg.save()

    def post(self, event="public_url_active", url=None, **extra):
        body = {"event": event, "broadcaster_id": "b1", "session_id": "s1", "name": "Studio", "stream_url": self.STREAM, **extra}
        return self.client.post(url or self.NEW, json.dumps(body), content_type="application/json")

    def files_fields(self, **extra):
        return {"radio_on_air": True, "files_enabled": True, "files_protected": True, "files_url": self.FILES, "info_url": self.INFO, **extra}

    def listed(self):
        return self.client.get("/app/v1/radio/channels").json()

    def test_new_address_registers_a_station(self):
        self.assertEqual(self.post().status_code, 200)
        self.assertEqual(self.client.get(self.NEW).status_code, 405)
        self.assertEqual([c["name"] for c in self.listed()["channels"]], ["Studio"])

    def test_old_address_is_gone(self):
        self.assertEqual(self.post(url="/radio/ingest").status_code, 404)
        self.assertFalse(RadioChannel.objects.exists())

    def test_channels_keep_the_exact_shape_apps_up_to_7_1_read(self):
        self.post(**self.files_fields())
        self.assertEqual(
            set(self.listed()["channels"][0]),
            {"id", "name", "stream_url", "ws_url", "now_playing", "listeners", "since"},
        )

    def test_files_only_station_is_never_a_channel_for_older_apps(self):
        self.post(**self.files_fields(radio_on_air=False))
        data = self.listed()
        self.assertEqual(data["channels"], [])
        self.assertEqual([s["files_url"] for s in data["stations"]], [self.FILES])
        self.assertFalse(data["stations"][0]["radio_on_air"])

    def test_phones_get_what_the_station_reports(self):
        self.post(**self.files_fields())
        station = self.listed()["stations"][0]
        self.assertTrue(station["radio_on_air"] and station["files_enabled"] and station["files_protected"])
        self.assertEqual((station["files_url"], station["info_url"]), (self.FILES, self.INFO))

    def test_open_share_is_passed_on_as_open(self):
        self.post(**self.files_fields(files_protected=False))
        station = self.listed()["stations"][0]
        self.assertTrue(station["files_enabled"])
        self.assertFalse(station["files_protected"])

    def test_station_offering_nothing_is_not_listed(self):
        self.post(radio_on_air=False, files_enabled=False)
        data = self.listed()
        self.assertEqual((data["channels"], data["stations"]), ([], []))

    def test_files_supported_is_in_every_reply(self):
        self.assertIs(self.post("test").json()["files_supported"], True)
        self.assertIs(self.post().json()["files_supported"], True)
        self.assertIs(self.post("heartbeat").json()["files_supported"], True)
        self.assertIs(self.post("public_url_inactive").json()["files_supported"], True)

    def test_heartbeat_from_an_older_audiosync_keeps_what_the_station_offers(self):
        self.post(**self.files_fields())
        self.post("heartbeat")
        row = RadioChannel.objects.get(broadcaster_id="b1")
        self.assertTrue(row.radio_on_air and row.files_enabled and row.files_protected)
        self.assertEqual((row.files_url, row.info_url), (self.FILES, self.INFO))

    def test_heartbeat_updates_what_the_station_offers(self):
        self.post(**self.files_fields())
        self.post("heartbeat", radio_on_air=False, files_protected=False)
        row = RadioChannel.objects.get(broadcaster_id="b1")
        self.assertFalse(row.radio_on_air or row.files_protected)
        self.post("heartbeat", files_enabled=False)
        self.assertEqual(RadioChannel.objects.get(broadcaster_id="b1").files_url, "")

    def test_go_live_from_an_older_audiosync_clears_the_old_files_details(self):
        self.post(**self.files_fields(radio_on_air=False))
        self.post()
        row = RadioChannel.objects.get(broadcaster_id="b1")
        self.assertTrue(row.radio_on_air)
        self.assertFalse(row.files_enabled)
        self.assertEqual(row.files_url, "")

    def test_files_address_on_another_host_is_dropped(self):
        self.post(**self.files_fields(files_url="https://elsewhere.example/api/files/", info_url="ftp://abc.trycloudflare.com/x"))
        row = RadioChannel.objects.get(broadcaster_id="b1")
        self.assertEqual((row.files_url, row.info_url), ("", ""))

    def test_values_that_are_not_true_or_false_are_ignored(self):
        self.post(radio_on_air="false", files_enabled=1)
        row = RadioChannel.objects.get(broadcaster_id="b1")
        self.assertTrue(row.radio_on_air)
        self.assertFalse(row.files_enabled)

    def test_admin_page_shows_the_new_address_and_the_share_state_with_no_switch(self):
        self.post(**self.files_fields())
        self.client.force_login(get_user_model().objects.create_superuser("boss", "boss@example.org", "pw"))
        page = self.client.get("/mobile/radio")
        self.assertContains(page, "/station/presence")
        self.assertContains(page, "Shared · PIN")
        self.assertNotContains(page, "Turn on")

    def test_live_rows_follow_the_station(self):
        self.post(**self.files_fields(now_playing="First.mp3"))
        self.client.force_login(get_user_model().objects.create_superuser("boss", "boss@example.org", "pw"))
        rows = self.client.get("/mobile/radio/rows")
        self.assertContains(rows, "First.mp3")
        self.assertContains(rows, "csrfmiddlewaretoken")  # Remove still works in rows fetched later
        self.assertNotContains(rows, "<html")
        self.post("heartbeat", now_playing="Second.mp3", radio_on_air=False, files_protected=False)
        rows = self.client.get("/mobile/radio/rows")
        self.assertContains(rows, "Second.mp3")
        self.assertContains(rows, "Files only")
        self.assertContains(rows, "Shared · No PIN")

    def test_live_rows_are_for_a_superuser_only(self):
        self.assertEqual(self.client.get("/mobile/radio/rows").status_code, 302)
