"""The admin starting a chat with a signed-in user (the + on /mobile/chats).
Run: python manage.py test mobileapi.tests_chat_start"""
import json
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from .chat_views import make_open_token
from .models import AppAccount, AppAuthToken, AppChatMessage, AppDevice
from .views import _chat_unread_count

PEOPLE = "/mobile/chats/people.json"
LIST = "/mobile/chats/list.json"


def account(name, signed_in=True, **fields):
    a = AppAccount.objects.create(name=name, **fields)
    if signed_in:
        AppAuthToken.issue(a)
    return a


class ChatPeopleTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("root", password="x"))

    def names(self, **params):
        return [p["name"] for p in self.client.get(PEOPLE, params).json()["people"]]

    def test_lists_signed_in_people_a_to_z_whatever_the_case(self):
        account("bala", phone="+919000000001")
        account("Chitra", email="c@example.org")
        account("Asha", email="a@example.org")
        self.assertEqual(self.names(), ["Asha", "bala", "Chitra"])

    def test_leaves_out_anyone_not_signed_in(self):
        account("Never signed in", signed_in=False)
        AppAuthToken.objects.create(key="r" * 40, account=account("Signed out", signed_in=False), revoked=True)
        expired = account("Expired", signed_in=False)
        AppAuthToken.objects.create(key="e" * 40, account=expired, expires_at=timezone.now() - timedelta(days=1))
        account("Disabled", is_active=False)
        account("Here")
        self.assertEqual(self.names(), ["Here"])

    def test_a_token_with_no_expiry_still_counts_because_it_still_works(self):
        a = account("Old token", signed_in=False)
        AppAuthToken.objects.create(key="o" * 40, account=a, expires_at=None)
        self.assertEqual(self.names(), ["Old token"])

    def test_leaves_out_support_agents_who_could_never_see_it(self):
        account("Agent", admin_chat_mode=True)
        account("Here")
        self.assertEqual(self.names(), ["Here"])

    def test_many_tokens_still_list_a_person_once(self):
        a = account("Two phones")
        AppAuthToken.issue(a)
        self.assertEqual(self.names(), ["Two phones"])

    def test_search_covers_name_email_phone_and_username(self):
        account("Asha", email="asha@example.org", phone="+919000000001", username="ashar")
        account("Bala", email="bala@example.org")
        for q in ("ASHA", "asha@ex", "9000000001", "ashar"):
            self.assertEqual(self.names(q=q), ["Asha"], q)
        self.assertEqual(self.names(q="nobody"), [])

    def test_a_long_list_is_capped_but_the_total_is_true(self):
        for i in range(53):
            account(f"Person {i:02}")
        d = self.client.get(PEOPLE).json()
        self.assertEqual((len(d["people"]), d["total"]), (50, 53))

    def test_only_the_admin_can_see_who_is_signed_in(self):
        account("Here")
        self.assertEqual(Client().get(PEOPLE).status_code, 302)
        plain = Client()
        plain.force_login(get_user_model().objects.create_user("staff", password="x"))
        self.assertEqual(plain.get(PEOPLE).status_code, 302)

    def test_the_page_has_the_plus_bubble(self):
        self.assertContains(self.client.get("/mobile/chats"), 'id="newChatBtn"')


class StartChatTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("root", password="x"))
        self.asha = account("Asha", email="a@example.org")

    def send(self, body="Hello Asha"):
        return self.client.post(f"/mobile/chats/{self.asha.id}/reply", data=json.dumps({"body": body}),
                                content_type="application/json")

    def conversations(self):
        return [c["id"] for c in self.client.get(LIST).json()["conversations"]]

    def test_opening_a_person_alone_does_not_create_a_chat(self):
        r = self.client.get(f"/mobile/chats/{self.asha.id}/messages.json")
        self.assertEqual(r.json()["messages"], [])
        self.assertEqual(r.json()["account"]["name"], "Asha")
        self.assertEqual(self.conversations(), [])

    def test_the_first_message_starts_the_chat_and_the_user_sees_it(self):
        with mock.patch("mobileapi.admin_views.fcm.push"):
            self.assertEqual(self.send().status_code, 200)
        self.assertEqual(self.conversations(), [self.asha.id])
        self.assertEqual(_chat_unread_count(self.asha), 1)

        app = Client()
        app.get("/chat/open", {"t": make_open_token(self.asha)})
        self.assertEqual([m["body"] for m in app.get("/chat/messages").json()["messages"]], ["Hello Asha"])

    def test_the_first_message_notifies_their_phone(self):
        AppDevice.objects.create(account=self.asha, device_id="d1", fcm_token="tok1")
        with mock.patch("mobileapi.admin_views.fcm.push") as push:
            self.send()
        self.assertEqual(push.call_args.args[0], ["tok1"])

    def test_someone_with_a_chat_keeps_that_one_chat(self):
        AppChatMessage.objects.create(account=self.asha, sender="user", body="Earlier")
        with mock.patch("mobileapi.admin_views.fcm.push"):
            self.send()
        self.assertEqual(self.conversations(), [self.asha.id])
        self.assertEqual(self.asha.chat_messages.count(), 2)
