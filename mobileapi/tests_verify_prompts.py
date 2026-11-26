"""Verify test: the Recent prompts table is live like Accounts, and prompts (test and partner) are removed by
the daily cleanup on the push log's days (AppConfig.cleanup_log_days).
Run: python manage.py test mobileapi.tests_verify_prompts"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .cleanup import run_cleanup
from .models import AppAccount, AppActionRequest, AppConfig, AppPartner


def set_days(days):
    cfg = AppConfig.load()
    cfg.cleanup_log_days = days
    cfg.save()


class PromptCleanupTests(TestCase):
    def setUp(self):
        self.asha = AppAccount.objects.create(name="Asha", email="asha@example.org")
        self.partner, self.key = AppPartner.issue("GSTSync")

    def prompt(self, days_old, status="completed", partner=None):
        p = AppActionRequest.objects.create(
            account=self.asha, partner=partner, action_type="approve", title="Approve?", status=status,
            expires_at=timezone.now() - timedelta(days=days_old) + timedelta(minutes=5),
        )
        AppActionRequest.objects.filter(pk=p.pk).update(created_at=timezone.now() - timedelta(days=days_old))
        return p

    def test_old_prompts_go_whatever_their_status_or_source(self):
        set_days(30)
        self.prompt(31)
        self.prompt(40, status="pending")
        self.prompt(90, status="expired", partner=self.partner)
        keep = [self.prompt(29), self.prompt(0, status="pending", partner=self.partner)]
        self.assertEqual(run_cleanup()["verify_prompts"], 3)
        self.assertEqual(sorted(AppActionRequest.objects.values_list("pk", flat=True)), sorted(p.pk for p in keep))

    def test_zero_keeps_them_all(self):
        set_days(0)
        self.prompt(400)
        self.assertNotIn("verify_prompts", run_cleanup())
        self.assertEqual(AppActionRequest.objects.count(), 1)

    @override_settings(CRON_KEY="k")
    def test_every_cleanup_cron_run_does_it_and_says_so(self):
        set_days(30)
        self.prompt(45)
        self.assertEqual(self.client.get("/cron/cleanup", HTTP_X_CRON_KEY="k").json()["verify_prompts"], 1)

    def test_a_partner_asking_about_a_removed_prompt_gets_not_found(self):
        cache.clear()
        set_days(30)
        old = self.prompt(45, partner=self.partner)
        run_cleanup()
        r = self.client.get(f"/partner/v1/actions/{old.pk}", HTTP_AUTHORIZATION=f"Bearer {self.key}")
        self.assertEqual(r.status_code, 404)


class LiveTableTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("boss", password="pw"))
        self.asha = AppAccount.objects.create(name="Asha", email="asha@example.org")

    def page(self, **headers):
        return self.client.get(reverse("mobile_action_test"), **headers).content.decode()

    def test_the_table_refreshes_itself_like_accounts(self):
        page = self.page()
        self.assertIn('<div class="card" data-table data-live="10">', page)
        self.assertIn('<tbody id="prompt-rows" data-live-part>', page)
        self.assertIn('<div class="table-foot" id="prompt-foot" data-live-part>', page)
        self.assertIn("data-live-toggle", page)
        self.assertNotIn("Refresh</a>", page)

    def test_a_refresh_brings_the_phones_answer(self):
        p = AppActionRequest.objects.create(account=self.asha, action_type="approve", title="Approve?",
                                            expires_at=timezone.now() + timedelta(minutes=5))
        self.assertIn("Pending", self.page(HTTP_X_LIVE_REFRESH="1"))
        AppActionRequest.objects.filter(pk=p.pk).update(status="completed", response={"value": "approved"})
        fresh = self.page(HTTP_X_LIVE_REFRESH="1")
        self.assertIn("Completed", fresh)
        self.assertIn("<code>approved</code>", fresh)

    def test_the_page_says_how_long_prompts_are_kept(self):
        set_days(30)
        self.assertIn("Prompts older than 30 days are removed by the daily cleanup.", self.page())
        set_days(0)
        self.assertIn("Prompts are kept forever: log cleanup is turned off.", self.page())
