"""An account's Links card: a row opens the link's settings, and its chips open the link itself in a new tab.
Run: python manage.py test mobileapi.tests_account_links"""
import re

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import AppAccount, AppLink, AppPartner


class AccountLinksCardTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("boss", "boss@example.org", "pw"))
        self.ravi = AppAccount.objects.create(name="Ravi", email="ravi@example.com")
        self.hr = AppLink.objects.create(account=self.ravi, title="HR Portal", url="https://hr.example.com/a?x=1&y=2")

    def page(self):
        return self.client.get(reverse("mobile_account_edit", args=[self.ravi.id])).content.decode()

    def chips(self):
        return re.search(r'<a class="ln-go"[^>]*>(.*?)</a>', self.page(), re.S)

    def test_the_row_opens_the_settings(self):
        row = re.search(r'<a class="[^"]*ln-row"[^>]*>', self.page()).group(0)
        edit = reverse("mobile_link_edit", args=[self.hr.id])
        self.assertIn(f'href="{edit}"', row)
        self.assertNotIn("_blank", row)
        self.assertEqual(self.client.get(edit).status_code, 200)

    def test_the_chips_open_the_link_itself_in_a_new_tab(self):
        opener = self.chips().group(0)
        self.assertIn('href="https://hr.example.com/a?x=1&amp;y=2"', opener)
        self.assertIn('target="_blank"', opener)
        self.assertIn('rel="noopener noreferrer"', opener)
        self.assertIn("Admin", self.chips().group(1))

    def test_a_partners_chip_and_the_off_chip_open_it_too(self):
        partner = AppPartner.objects.create(name="GSTSYNC", api_key_hash="h" * 64)
        AppLink.objects.filter(pk=self.hr.pk).update(is_active=False, partner=partner)
        inside = self.chips().group(1)
        self.assertIn('badge-danger">Off<', inside)
        self.assertIn("GSTSYNC", inside)
        self.assertNotIn("Admin", inside)

    def test_there_is_no_separate_edit_button(self):
        self.assertNotIn("ln-edit", self.page())
