"""Live stations for everyone (GET /app/v1/radio/channels, no sign-in) and AudioSync's presence events.
Run: python manage.py test mobileapi.tests_radio"""
import hashlib
import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from .models import AppConfig, RadioChannel
from .radio import station_page, station_public_id


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

    def test_phones_get_the_wifi_address_and_the_id_audiosync_answers_with(self):
        self.post(**self.files_fields(lan_url="http://192.168.1.7:8000/"))
        station = self.listed()["stations"][0]
        self.assertEqual(station["lan_url"], "http://192.168.1.7:8000/")
        # The same sum as AudioSync's config.station_id, and never the private broadcaster_id.
        self.assertEqual(station["station_id"], hashlib.sha256(b"audiosync-station:b1").hexdigest()[:16])
        self.assertNotIn("broadcaster_id", station)

    def test_a_station_cannot_choose_its_own_id(self):
        self.post(**self.files_fields(station_id="0000000000000000"))
        self.assertNotEqual(self.listed()["stations"][0]["station_id"], "0000000000000000")

    def test_older_apps_never_see_the_wifi_address(self):
        self.post(**self.files_fields(lan_url="http://192.168.1.7:8000/"))
        self.assertNotIn("lan_url", self.listed()["channels"][0])

    def test_only_a_private_ipv4_address_with_a_port_is_kept(self):
        for bad in (
            "https://192.168.1.7:8000/",  # AudioSync's Wi-Fi address is plain http
            "http://8.8.8.8:8000/",  # on the internet
            "http://example.com:8000/",  # a name can point anywhere
            "http://127.0.0.1:8000/",  # the phone itself
            "http://169.254.169.254:80/",  # link-local
            "http://100.64.0.1:8000/",  # a carrier's network
            "http://[fd00::1]:8000/",
            "http://192.168.1.7/",  # no port
            "http://192.168.1.7:0/",
            "http://192.168.1.7:99999/",
            "http://user:pw@192.168.1.7:8000/",
            "http://192.168.1.7:8000/other",
            "http://192.168.1.7:8000/?x=1",
            "ftp://192.168.1.7:8000/",
            "not a url",
            "",
            123,
            None,
        ):
            with self.subTest(bad=bad):
                self.post(**self.files_fields(lan_url=bad))
                self.assertEqual(self.listed()["stations"][0]["lan_url"], "")
        for good in ("http://10.0.0.5:8000/", "http://172.16.0.9:8000/", "http://172.31.255.1:80", "http://192.168.0.2:65535/"):
            with self.subTest(good=good):
                self.post(**self.files_fields(lan_url=good))
                self.assertEqual(self.listed()["stations"][0]["lan_url"], good.rstrip("/") + "/")

    def test_the_wifi_address_follows_the_heartbeat_and_goes_when_the_pc_has_no_network(self):
        self.post(**self.files_fields(lan_url="http://192.168.1.7:8000/"))
        self.post("heartbeat", **self.files_fields(lan_url="http://192.168.1.9:8000/"))
        self.assertEqual(self.listed()["stations"][0]["lan_url"], "http://192.168.1.9:8000/")
        self.post("heartbeat", **self.files_fields())
        self.assertEqual(self.listed()["stations"][0]["lan_url"], "")

    def test_a_new_go_live_replaces_the_wifi_address(self):
        self.post(**self.files_fields(lan_url="http://192.168.1.7:8000/"))
        self.post(**self.files_fields())
        self.assertEqual(self.listed()["stations"][0]["lan_url"], "")

    def test_open_share_is_passed_on_as_open(self):
        self.post(**self.files_fields(files_protected=False))
        station = self.listed()["stations"][0]
        self.assertTrue(station["files_enabled"])
        self.assertFalse(station["files_protected"])

    def test_a_station_keeps_its_key_across_sessions_and_new_addresses(self):
        self.post(**self.files_fields())
        first = self.listed()["stations"][0]["key"]
        self.post("public_url_inactive")
        self.client.post(self.NEW, json.dumps({
            "event": "public_url_active", "broadcaster_id": "b1", "session_id": "s2", "name": "Studio",
            "stream_url": "https://other.trycloudflare.com/stream", "files_enabled": True,
        }), content_type="application/json")
        again = self.listed()["stations"][0]
        self.assertEqual(again["key"], first)
        self.assertNotIn("b1", again["key"])
        self.assertEqual(len(first), 24)

    def test_different_stations_get_different_keys(self):
        self.post(**self.files_fields())
        self.client.post(self.NEW, json.dumps({
            "event": "public_url_active", "broadcaster_id": "b2", "session_id": "x", "name": "Other",
            "stream_url": "https://zzz.trycloudflare.com/stream", "files_enabled": True,
        }), content_type="application/json")
        keys = {s["key"] for s in self.listed()["stations"]}
        self.assertEqual(len(keys), 2)

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

    def test_each_row_links_to_the_stations_page(self):
        self.post()
        self.client.force_login(get_user_model().objects.create_superuser("boss", "boss@example.org", "pw"))
        rows = self.client.get("/mobile/radio/rows")
        self.assertContains(rows, 'href="https://abc.trycloudflare.com/"')
        self.assertContains(rows, 'rel="noopener noreferrer"')

    def test_a_station_on_wifi_gets_its_own_button_for_the_page_to_check(self):
        self.post(**self.files_fields(lan_url="http://192.168.1.7:8000/"))
        self.client.force_login(get_user_model().objects.create_superuser("boss", "boss@example.org", "pw"))
        rows = self.client.get("/mobile/radio/rows")
        self.assertContains(rows, 'href="http://192.168.1.7:8000/"')
        self.assertContains(rows, f'data-station-id="{station_public_id("b1")}"')
        self.assertContains(rows, "192.168.1.7:8000", count=3)  # the link, its title and the line under the name
        self.assertContains(rows, 'href="https://abc.trycloudflare.com/"')  # the public one stays, separately
        self.assertContains(self.client.get("/mobile/radio"), "targetAddressSpace")

    def test_a_station_without_a_wifi_address_gets_no_such_button(self):
        for sent in ({}, {"lan_url": "http://8.8.8.8:8000/"}):
            with self.subTest(sent=sent):
                self.post(**self.files_fields(**sent))
                self.client.force_login(get_user_model().objects.get_or_create(
                    username="boss", defaults={"email": "boss@example.org", "is_superuser": True, "is_staff": True})[0])
                self.assertNotContains(self.client.get("/mobile/radio/rows"), "data-wifi-link")

    def test_a_row_with_an_unsafe_address_gets_no_link(self):
        for bad in ("javascript:alert(1)", "file:///etc/passwd", "https://", "http://[bad"):
            self.assertEqual(station_page(bad), "", bad)
        RadioChannel.objects.create(broadcaster_id="b2", name="Odd", stream_url="javascript:alert(1)")
        self.client.force_login(get_user_model().objects.create_superuser("boss", "boss@example.org", "pw"))
        self.assertNotContains(self.client.get("/mobile/radio/rows"), "href=\"javascript")

    def test_heartbeat_ignores_an_audio_address_that_is_not_a_web_address(self):
        self.post()
        self.post("heartbeat", stream_url="javascript:alert(1)")
        self.assertEqual(RadioChannel.objects.get(broadcaster_id="b1").stream_url, self.STREAM)
        self.post("heartbeat", stream_url="https://new.trycloudflare.com/stream")
        self.assertEqual(RadioChannel.objects.get(broadcaster_id="b1").stream_url, "https://new.trycloudflare.com/stream")

    def test_live_rows_are_for_a_superuser_only(self):
        self.assertEqual(self.client.get("/mobile/radio/rows").status_code, 302)
