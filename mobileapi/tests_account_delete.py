"""Delete account from the app: the account and everything kept for it are erased for good.
Run: python manage.py test mobileapi.tests_account_delete"""
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from . import fcm
from .models import (AppAccount, AppAuthToken, AppChatMessage, AppConfig, AppDevice, AppLink, AppNotificationLog,
                     AppReminder, SyncItem)


@mock.patch.object(fcm, "is_configured", lambda: False)  # never reach Firebase from tests
class DeleteAccountTests(TestCase):
    def setUp(self):
        AppConfig.load()
        self.ravi = AppAccount(name="Ravi", email="ravi@example.com", phone="9876543210")
        self.ravi.set_password("secret123")
        self.ravi.save()
        self.priya = AppAccount(name="Priya", email="priya@example.com")
        self.priya.set_password("secret123")
        self.priya.save()
        self.token = AppAuthToken.issue(self.ravi)
        AppLink.objects.create(account=self.ravi, title="HR Portal", url="https://hr.example.com/")
        AppLink.objects.create(title="GST Portal", url="https://www.gst.gov.in/")  # a general link: not his
        SyncItem.objects.create(account=self.ravi, kind="bookmark", key="k1", data={"url": "https://a.example/"})
        SyncItem.objects.create(account=self.priya, kind="bookmark", key="k1", data={"url": "https://b.example/"})
        AppChatMessage.objects.create(account=self.ravi, sender="user", body="Hello")
        AppReminder.objects.create(account=self.ravi, title="Pay bill", body="Today", scheduled_at=timezone.now())
        AppNotificationLog.objects.create(account=self.ravi, title="For Ravi", body="Just him")
        AppNotificationLog.objects.create(title="Everyone", body="A broadcast")
        self.phone = AppDevice.objects.create(device_id="phone-1", account=self.ravi)

    def delete(self, key=None):
        return self.client.post("/app/v1/account/delete", HTTP_AUTHORIZATION=f"Bearer {key or self.token.key}")

    def test_everything_of_his_is_gone(self):
        self.assertEqual(self.delete().status_code, 200)
        self.assertFalse(AppAccount.objects.filter(pk=self.ravi.pk).exists())
        self.assertFalse(AppAuthToken.objects.filter(account_id=self.ravi.pk).exists())
        self.assertEqual(list(AppLink.objects.values_list("title", flat=True)), ["GST Portal"])
        self.assertFalse(SyncItem.objects.filter(account_id=self.ravi.pk).exists())
        self.assertFalse(AppChatMessage.objects.exists())
        self.assertFalse(AppReminder.objects.exists())
        self.assertEqual(list(AppNotificationLog.objects.values_list("title", flat=True)), ["Everyone"])

    def test_others_and_his_phone_stay(self):
        self.delete()
        self.assertTrue(AppAccount.objects.filter(pk=self.priya.pk).exists())
        self.assertEqual(SyncItem.objects.filter(account=self.priya).count(), 1)
        self.phone.refresh_from_db()
        self.assertIsNone(self.phone.account)  # a signed-out install now, like after a sign-out

    def test_the_old_token_no_longer_works(self):
        key = self.token.key
        self.delete()
        self.assertEqual(self.client.get("/app/v1/sync", HTTP_AUTHORIZATION=f"Bearer {key}").status_code, 401)

    def test_the_email_and_phone_are_free_again(self):
        self.delete()
        again = AppAccount(name="Ravi again", email="ravi@example.com", phone="9876543210")
        again.set_password("secret456")
        again.save()  # unique email / phone: no clash with the deleted account
        self.assertEqual(AppAccount.objects.filter(email="ravi@example.com").count(), 1)
