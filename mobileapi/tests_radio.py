"""Live stations for everyone (GET /app/v1/radio/channels, no sign-in).
Run: python manage.py test mobileapi.tests_radio"""
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
