"""Clear chat on /mobile/chats: every message deleted at once, for the user too, by the admin only.
Run: python manage.py test mobileapi.tests_chat_clear"""
import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from .chat_views import make_open_token
from .models import AppAccount, AppAuthToken, AppChatMessage, AppNotificationLog
from .views import _chat_unread_count

LIST = "/mobile/chats/list.json"


class ClearChatTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("root", password="x"))
        self.asha = AppAccount.objects.create(name="Asha", email="a@example.org")
        self.say("user", "Hello")
        self.say("admin", "Hi Asha", read_by_admin=True)

    def say(self, sender, body, **fields):
        return AppChatMessage.objects.create(account=self.asha, sender=sender, body=body, **fields)

    def clear(self):
        return self.client.post(f"/mobile/chats/{self.asha.id}/clear")

    def listed(self):
        return [c["id"] for c in self.client.get(LIST).json()["conversations"]]

    def app(self):
        client = Client()
        client.get("/chat/open", {"t": make_open_token(self.asha)})
        return client

    def test_every_message_is_really_deleted(self):
        self.assertEqual(self.clear().status_code, 200)
        self.assertFalse(AppChatMessage.objects.filter(account=self.asha).exists())

    def test_the_console_shows_nothing_for_them(self):
        self.clear()
        self.assertEqual(self.client.get(f"/mobile/chats/{self.asha.id}/messages.json").json()["messages"], [])

    def test_they_leave_the_conversation_list(self):
        self.assertIn(self.asha.id, self.listed())
        self.clear()
        self.assertNotIn(self.asha.id, self.listed())

    def test_a_signed_in_person_can_still_be_found_with_the_plus(self):
        AppAuthToken.issue(self.asha)
        self.clear()
        people = self.client.get("/mobile/chats/people.json").json()["people"]
        self.assertEqual([p["name"] for p in people], ["Asha"])

    def test_nothing_records_that_it_was_cleared(self):
        before = {f.name: getattr(self.asha, f.attname) for f in AppAccount._meta.concrete_fields}
        self.clear()
        self.asha.refresh_from_db()
        after = {f.name: getattr(self.asha, f.attname) for f in AppAccount._meta.concrete_fields}
        self.assertEqual(after, before)

    def test_they_are_gone_from_the_users_app_too(self):
        app = self.app()
        self.assertEqual(len(app.get("/chat/messages").json()["messages"]), 2)
        self.clear()
        self.assertEqual(app.get("/chat/messages").json()["messages"], [])
        self.assertEqual(_chat_unread_count(self.asha), 0)

    def test_a_chat_screen_left_open_can_see_its_messages_are_gone(self):
        app = self.app()
        shown = app.get("/chat/messages").json()["latest_id"]
        self.assertGreater(shown, 0)
        self.clear()
        self.assertLess(app.get("/chat/messages", {"since": shown}).json()["latest_id"], shown)

    def test_an_open_screen_is_not_emptied_when_nothing_was_cleared(self):
        app = self.app()
        shown = app.get("/chat/messages").json()["latest_id"]
        self.assertEqual(app.get("/chat/messages", {"since": shown}).json()["latest_id"], shown)
        self.say("user", "One more")
        self.assertGreater(app.get("/chat/messages", {"since": shown}).json()["latest_id"], shown)

    def test_their_words_also_leave_the_push_log(self):
        AppNotificationLog.objects.create(source="chat", account=self.asha, title="New message from Admin", body="Hi Asha")
        AppNotificationLog.objects.create(source="admin", account=self.asha, title="Offer", body="Keep this")
        self.clear()
        self.assertEqual(list(AppNotificationLog.objects.values_list("body", flat=True)), ["Keep this"])

    def test_a_new_message_from_the_user_brings_them_back(self):
        self.clear()
        self.say("user", "Are you there?")
        self.assertIn(self.asha.id, self.listed())
        d = self.client.get(f"/mobile/chats/{self.asha.id}/messages.json").json()
        self.assertEqual([m["body"] for m in d["messages"]], ["Are you there?"])

    def test_the_admin_can_write_to_them_again(self):
        self.clear()
        with mock.patch("mobileapi.admin_views.fcm.push"):
            r = self.client.post(f"/mobile/chats/{self.asha.id}/reply", data=json.dumps({"body": "Back"}),
                                 content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.assertIn(self.asha.id, self.listed())
        self.assertEqual([m["body"] for m in self.app().get("/chat/messages").json()["messages"]], ["Back"])

    def test_it_leaves_other_people_alone(self):
        ravi = AppAccount.objects.create(name="Ravi", email="r@example.org")
        AppChatMessage.objects.create(account=ravi, sender="user", body="Mine")
        AppNotificationLog.objects.create(source="chat", account=ravi, title="t", body="Ravi's")
        self.clear()
        self.assertEqual(ravi.chat_messages.count(), 1)
        self.assertEqual(AppNotificationLog.objects.filter(account=ravi).count(), 1)
        self.assertEqual(self.listed(), [ravi.id])

    def test_clearing_an_empty_chat_is_harmless(self):
        self.clear()
        self.assertEqual(self.clear().status_code, 200)


class OnlyTheAdminCanClearTests(TestCase):
    def setUp(self):
        self.asha = AppAccount.objects.create(name="Asha", email="a@example.org")
        AppChatMessage.objects.create(account=self.asha, sender="user", body="Hello")
        self.url = f"/mobile/chats/{self.asha.id}/clear"

    def assertNotCleared(self):
        self.assertEqual(self.asha.chat_messages.count(), 1)

    def test_signed_out_cannot(self):
        self.assertEqual(Client().post(self.url).status_code, 302)
        self.assertNotCleared()

    def test_a_console_user_who_is_not_admin_cannot(self):
        c = Client()
        c.force_login(get_user_model().objects.create_user("staff", password="x", is_staff=True))
        self.assertEqual(c.post(self.url).status_code, 302)
        self.assertNotCleared()

    def test_the_app_user_and_a_support_agent_cannot(self):
        agent = AppAccount.objects.create(name="Agent", email="g@example.org", admin_chat_mode=True)
        for who in (self.asha, agent):
            c = Client()
            c.get("/chat/open", {"t": make_open_token(who)})
            self.assertEqual(c.post(self.url).status_code, 302)
        self.assertNotCleared()

    def test_a_plain_link_cannot_clear_it(self):
        c = Client()
        c.force_login(get_user_model().objects.create_superuser("root", password="x"))
        self.assertEqual(c.get(self.url).status_code, 405)
        self.assertNotCleared()

    def test_only_the_admin_page_has_the_button(self):
        c = Client()
        c.force_login(get_user_model().objects.create_superuser("root", password="x"))
        self.assertContains(c.get("/mobile/chats"), 'id="clearChat"')

        app = Client()
        app.get("/chat/open", {"t": make_open_token(self.asha)})
        self.assertNotContains(app.get("/chat/"), "clearChat")

        agent = AppAccount.objects.create(name="Agent", email="g@example.org", admin_chat_mode=True)
        inbox = Client()
        inbox.get("/chat/open", {"t": make_open_token(agent)})
        self.assertNotContains(inbox.get("/chat/inbox/"), "clearChat")
