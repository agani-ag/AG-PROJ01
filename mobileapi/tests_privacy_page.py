"""The public privacy policy page (the URL pasted into Play Console).
Run: python manage.py test mobileapi.tests_privacy_page"""
import re

from django.test import TestCase

from .models import AppConfig


class PrivacyPageTests(TestCase):
    def page(self):
        r = self.client.get("/privacy/")
        self.assertEqual(r.status_code, 200)
        return r.content.decode()

    def test_anyone_can_open_it_signed_out(self):
        self.page()

    def test_it_shows_the_details_from_app_config(self):
        cfg = AppConfig.load()
        cfg.privacy_company_name = "Acme Ltd"
        cfg.privacy_effective_date = "1 January 2030"
        cfg.privacy_contact_email = "privacy@example.org"
        cfg.save()
        html = self.page()
        self.assertIn("Privacy Policy — Acme Ltd (SyncUp)</title>", html)
        self.assertIn("Effective date: 1 January 2030", html)
        self.assertIn('href="mailto:privacy@example.org"', html)
        self.assertIn("com.agani.syncup", html)

    def test_it_stays_light_whatever_the_phone_is_set_to(self):
        html = self.page()
        self.assertIn("color-scheme: only light", html)
        self.assertNotIn("prefers-color-scheme: dark", html)

    def test_every_contents_link_goes_to_a_section(self):
        html = self.page()
        contents = html.split('id="toc"')[1].split("</details>")[0]
        targets = re.findall(r'href="#([^"]+)"', contents)
        self.assertEqual(len(targets), 10)
        for target in targets:
            self.assertIn(f'<section id="{target}">', html)

    def test_the_deletion_section_has_a_link_it_can_be_shared_by(self):
        self.assertIn('<section id="data-retention-and-deletion">', self.page())

    def test_the_promises_are_all_still_there(self):
        html = self.page()
        for promise in ("we do not store your IP address", "Settings → Delete", "Google Privacy Policy",
                        "Physical activity", "Run after reboot"):
            self.assertIn(promise.lower(), re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).lower(), promise)
