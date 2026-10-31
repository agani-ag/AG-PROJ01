"""The force-update minimum picked by release name ("7.1") and stored as its Play version code (9).
Run: python manage.py test mobileapi.tests_app_versions"""
from django.contrib.auth import get_user_model
from django.test import TestCase

from .app_versions import code_for, minimum_choices, name_for
from .models import AppConfig, AppDevice


class AppVersionTests(TestCase):
    def setUp(self):
        for i, version in enumerate(["7.1", "7.1", "7", "6", "3", None]):
            AppDevice.objects.create(device_id=f"phone-{i}", app_version=version)

    def test_names_map_to_play_codes(self):
        self.assertEqual(code_for("7.1"), 9)
        self.assertEqual(code_for("7"), 8)
        self.assertEqual(code_for("6"), 6)
        self.assertEqual(code_for("3"), 3, "an old build not in the list counts as its major number")
        self.assertEqual(code_for(None), 0)
        self.assertEqual(name_for(9), "7.1")

    def test_choices_say_how_many_phones_would_be_blocked(self):
        labels = dict(minimum_choices(7))
        self.assertTrue(labels[0].startswith("No forced update"))
        self.assertIn("7.1 or newer (code 9) — 4 phones would have to update", labels[9])
        self.assertIn("7 or newer (code 8) — 3 phones", labels[8])
        self.assertIn("Code 7, as saved — 3 phones", labels[7])


class ConfigPageTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("boss", "boss@example.com", "pw-123456"))
        cfg = AppConfig.load()
        cfg.min_supported_version = 7
        cfg.save()

    def test_picking_a_release_saves_its_code(self):
        page = self.client.get("/mobile/config")
        self.assertContains(page, "7.1 or newer (code 9)")
        data = {k: v for k, v in page.context["form"].initial.items() if v is not None}
        data.update(min_supported_version="9", android_package_name="com.agani.syncup")
        for flag in ("announcement_active", "announcement_fullscreen", "announcement_blocking", "chat_enabled", "signup_enabled"):
            if not data.get(flag):
                data.pop(flag, None)
        if isinstance(data.get("feature_flags"), dict):
            import json
            data["feature_flags"] = json.dumps(data["feature_flags"])
        r = self.client.post("/mobile/config", data)
        self.assertEqual(r.status_code, 302, r.content[:600])
        self.assertEqual(AppConfig.load().min_supported_version, 9)

    def test_a_version_name_is_not_a_choice(self):
        r = self.client.post("/mobile/config", {"min_supported_version": "7.1", "android_package_name": "com.agani.syncup"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(AppConfig.load().min_supported_version, 7)
