"""Live stations for everyone (GET /app/v1/radio/channels, no sign-in).
Run: python manage.py test mobileapi.tests_radio"""
import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from .models import AppConfig, RadioChannel, RadioFilesStation


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
    """AudioSync's presence events: the address, what a station offers, and who hears about its files."""
    NEW = "/station/presence"
    OLD = "/radio/ingest"
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

    def test_new_address_registers_a_station_and_only_the_old_one_points_there(self):
        r = self.post()
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("moved_to", r.json())
        self.assertEqual(self.client.get(self.NEW).status_code, 405)
        self.assertEqual(self.post("heartbeat", url=self.OLD).json()["moved_to"], "http://testserver/station/presence")
        self.assertEqual([c["name"] for c in self.listed()["channels"]], ["Studio"])

    def test_old_address_still_registers_a_station(self):
        self.assertEqual(self.post(url=self.OLD).status_code, 200)
        self.assertTrue(RadioChannel.objects.filter(broadcaster_id="b1").exists())

    def test_channels_keep_the_exact_shape_apps_up_to_7_1_read(self):
        self.post(**self.files_fields())
        self.assertEqual(
            set(self.listed()["channels"][0]),
            {"id", "name", "stream_url", "ws_url", "now_playing", "listeners", "since"},
        )

    def test_files_only_station_is_never_a_channel_for_older_apps(self):
        RadioFilesStation.objects.create(broadcaster_id="b1")
        self.post(**self.files_fields(radio_on_air=False))
        data = self.listed()
        self.assertEqual(data["channels"], [])
        self.assertEqual([s["files_url"] for s in data["stations"]], [self.FILES])
        self.assertFalse(data["stations"][0]["radio_on_air"])

    def test_phones_hear_nothing_about_files_until_the_admin_switches_the_station_on(self):
        self.post(**self.files_fields())
        station = self.listed()["stations"][0]
        self.assertFalse(station["files_enabled"])
        self.assertFalse(station["files_protected"])
        self.assertEqual((station["files_url"], station["info_url"]), ("", ""))
        RadioFilesStation.objects.create(broadcaster_id="b1")
        station = self.listed()["stations"][0]
        self.assertTrue(station["files_enabled"])
        self.assertTrue(station["files_protected"])
        self.assertEqual((station["files_url"], station["info_url"]), (self.FILES, self.INFO))

    def test_files_only_station_not_switched_on_is_not_listed_at_all(self):
        self.post(**self.files_fields(radio_on_air=False))
        data = self.listed()
        self.assertEqual((data["channels"], data["stations"]), ([], []))

    def test_files_supported_is_true_only_for_a_station_switched_on(self):
        self.assertIs(self.post("test").json()["files_supported"], False)
        self.assertIs(self.post().json()["files_supported"], False)
        RadioFilesStation.objects.create(broadcaster_id="b1")
        self.assertIs(self.post("test").json()["files_supported"], True)
        self.assertIs(self.post().json()["files_supported"], True)
        self.assertIs(self.post("heartbeat").json()["files_supported"], True)

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

    def test_admin_page_shows_the_new_address_and_turns_files_on_and_off(self):
        self.post()
        channel = RadioChannel.objects.get(broadcaster_id="b1")
        self.client.force_login(get_user_model().objects.create_superuser("boss", "boss@example.org", "pw"))
        self.assertContains(self.client.get("/mobile/radio"), "/station/presence")
        self.client.post(f"/mobile/radio/channels/{channel.id}/files")
        self.assertEqual(RadioFilesStation.objects.get().broadcaster_id, "b1")
        channel.delete()
        self.post()
        self.assertTrue(RadioFilesStation.objects.filter(broadcaster_id="b1").exists())
        self.client.post(f"/mobile/radio/channels/{RadioChannel.objects.get().id}/files")
        self.assertFalse(RadioFilesStation.objects.exists())

    def test_only_a_superuser_can_turn_files_on(self):
        self.post()
        channel = RadioChannel.objects.get(broadcaster_id="b1")
        self.client.post(f"/mobile/radio/channels/{channel.id}/files")
        self.assertFalse(RadioFilesStation.objects.exists())
