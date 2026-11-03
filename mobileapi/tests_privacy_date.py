"""App config → Privacy policy: the effective date is picked from a calendar and saved as the text the
privacy page shows. Run: python manage.py test mobileapi.tests_privacy_date"""
import json
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase

from .forms import parse_privacy_date, privacy_date_text
from .models import AppConfig


class PrivacyDateTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("boss", "boss@example.com", "pw-123456"))

    def save(self, **changes):
        """Post the config page as it stands, with `changes`."""
        page = self.client.get("/mobile/config")
        data = {k: v for k, v in page.context["form"].initial.items() if v is not None}
        for flag in ("announcement_active", "announcement_fullscreen", "announcement_blocking", "chat_enabled", "signup_enabled"):
            if not data.get(flag):
                data.pop(flag, None)
        if isinstance(data.get("feature_flags"), dict):
            data["feature_flags"] = json.dumps(data["feature_flags"])
        data.update(changes)
        return self.client.post("/mobile/config", data)

    def set_text(self, text):
        cfg = AppConfig.load()
        cfg.privacy_effective_date = text
        cfg.save()

    def test_the_picker_shows_the_saved_date(self):
        self.set_text("04 November 2026")
        page = self.client.get("/mobile/config")
        self.assertContains(page, 'type="date"')
        self.assertContains(page, 'value="2026-11-04"')
        self.assertContains(page, "The page shows <code>04 November 2026</code>")

    def test_a_picked_date_is_saved_as_the_page_shows_it(self):
        r = self.save(privacy_effective_date="2026-11-04")
        self.assertEqual(r.status_code, 302, r.content[:600])
        self.assertEqual(AppConfig.load().privacy_effective_date, "4 November 2026")
        self.assertContains(self.client.get("/privacy/"), "Effective date: 4 November 2026")

    def test_left_empty_the_saved_text_stays(self):
        self.set_text("Sometime in October")  # an old free-text wording the picker can't read
        page = self.client.get("/mobile/config")
        self.assertNotContains(page, 'name="privacy_effective_date" value=')
        r = self.save(privacy_effective_date="", support_email="help@example.com")
        self.assertEqual(r.status_code, 302, "an unreadable old date never blocks saving the rest")
        cfg = AppConfig.load()
        self.assertEqual((cfg.privacy_effective_date, cfg.support_email), ("Sometime in October", "help@example.com"))

    def test_not_a_date_is_refused(self):
        self.set_text("10 August 2026")
        r = self.save(privacy_effective_date="2026-02-30")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(AppConfig.load().privacy_effective_date, "10 August 2026")

    def test_older_wordings_are_read(self):
        for text in ("10 August 2026", "10 Aug 2026", "2026-08-10", "August 10, 2026", "10/08/2026", " 10 August 2026 "):
            self.assertEqual(parse_privacy_date(text), date(2026, 8, 10), text)
        for text in ("", None, "soon", "31 June 2026"):
            self.assertIsNone(parse_privacy_date(text), text)
        self.assertEqual(privacy_date_text(date(2026, 11, 4)), "4 November 2026")
