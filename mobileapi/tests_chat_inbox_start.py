"""The app's support inbox (admin_chat_mode agents): the + to start a chat with anyone signed in, and the
Active now count — the same list and count as the console's Messages page.
Run: python manage.py test mobileapi.tests_chat_inbox_start"""
import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from .chat_views import make_open_token
from .models import AppAccount, AppAuthToken, AppChatMessage, AppDevice

PEOPLE = "/chat/inbox/people.json"
LIST = "/chat/inbox/list.json"


def person(name, open_now=False, signed_in=True, **fields):
    a = AppAccount.objects.create(name=name, email=f"{name.lower().replace(' ', '')}@example.org", **fields)
    if signed_in:
        AppAuthToken.issue(a)
    AppDevice.objects.create(account=a, device_id=f"{name}-1", fcm_token="t",
                             active_at=timezone.now() if open_now else None)
    return a


def opened_by(account):
    """A WebView session for this account, as the app opens the chat."""
    c = Client()
    c.get("/chat/open", {"t": make_open_token(account)})
    return c


class InboxPeopleTests(TestCase):
    def setUp(self):
        self.agent = person("Agent", admin_chat_mode=True)
        self.inbox = opened_by(self.agent)

    def people(self, **params):
        return self.inbox.get(PEOPLE, params).json()

    def test_active_now_first_then_everyone_else_each_a_to_z(self):
        person("bala")
        person("Chitra", open_now=True)
        person("Asha")
        person("Devi", open_now=True)
        d = self.people()
        self.assertEqual([p["name"] for p in d["people"]], ["Chitra", "Devi", "Asha", "bala"])
        self.assertEqual([p["online"] for p in d["people"]], [True, True, False, False])
        self.assertEqual((d["total"], d["active"]), (4, 2))

    def test_leaves_out_agents_including_this_one_and_anyone_not_signed_in(self):
        person("Other agent", admin_chat_mode=True)
        person("Never signed in", signed_in=False)
        person("Disabled", is_active=False)
        person("Here")
        self.assertEqual([p["name"] for p in self.people()["people"]], ["Here"])

    def test_search_by_name_email_or_phone(self):
        person("Asha", phone="+919000000001")
        person("Bala")
        self.assertEqual([p["name"] for p in self.people(q="9000000001")["people"]], ["Asha"])
        self.assertEqual([p["name"] for p in self.people(q="bala@")["people"]], ["Bala"])

    def test_same_list_as_the_console(self):
        person("Asha", open_now=True)
        person("Bala")
        console = Client()
        console.force_login(get_user_model().objects.create_superuser("root", password="x"))
        self.assertEqual(self.people(), console.get("/mobile/chats/people.json").json())

    def test_only_a_support_agent_gets_it(self):
        person("Asha")
        self.assertEqual(Client().get(PEOPLE).status_code, 401)
        self.assertEqual(opened_by(person("Plain user")).get(PEOPLE).status_code, 401)


class InboxActiveCountTests(TestCase):
    def setUp(self):
        self.agent = person("Agent", admin_chat_mode=True, open_now=True)  # an agent never counts
        self.inbox = opened_by(self.agent)

    def test_the_list_brings_the_active_now_count(self):
        person("Asha", open_now=True)
        person("Bala", open_now=True)
        person("Chitra")
        person("Signed out but open", open_now=True, signed_in=False)
        self.assertEqual(self.inbox.get(LIST).json()["active"], 2)

    def test_it_matches_the_console_menu_count(self):
        person("Asha", open_now=True)
        person("Bala")
        console = Client()
        console.force_login(get_user_model().objects.create_superuser("root", password="x"))
        self.assertEqual(self.inbox.get(LIST).json()["active"], console.get("/mobile/chats/counts.json").json()["active"])


class InboxStartChatTests(TestCase):
    """Picking someone from the + opens an empty chat; the first reply starts the conversation."""

    def setUp(self):
        self.agent = person("Agent", admin_chat_mode=True)
        self.inbox = opened_by(self.agent)
        self.asha = person("Asha")

    def test_a_new_chat_opens_empty_and_the_first_reply_lists_it(self):
        d = self.inbox.get(f"/chat/inbox/{self.asha.id}/messages.json").json()
        self.assertEqual((d["messages"], d["account"]["name"]), ([], "Asha"))
        self.assertEqual(self.inbox.get(LIST).json()["conversations"], [])
        with mock.patch("mobileapi.chat_views.fcm.push") as push:
            r = self.inbox.post(f"/chat/inbox/{self.asha.id}/reply", json.dumps({"body": "Hi Asha"}),
                                content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(AppChatMessage.objects.get(account=self.asha).sender, "admin")
        self.assertEqual([c["name"] for c in self.inbox.get(LIST).json()["conversations"]], ["Asha"])
        push.assert_called_once()

    def test_the_page_has_the_plus_and_the_active_count(self):
        html = self.inbox.get("/chat/inbox/").content.decode()
        for part in ('id="newChatBtn"', 'id="activeChip"', 'id="peopleSheet"', "/chat/inbox/people.json"):
            self.assertIn(part, html)
