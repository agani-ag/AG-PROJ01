"""Clear chat on /mobile/chats and in the app's support inbox: every message deleted at once, for the
user too, by the admin or a support agent (admin_chat_mode) only. Run: python manage.py test mobileapi.tests_chat_clear"""
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


class WhoCanClearOnTheConsoleTests(TestCase):
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

    def test_the_app_user_and_a_support_agent_cannot_use_the_console_address(self):
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

    def test_the_console_and_the_support_inbox_have_the_button_and_users_do_not(self):
        c = Client()
        c.force_login(get_user_model().objects.create_superuser("root", password="x"))
        self.assertContains(c.get("/mobile/chats"), 'id="clearChat"')

        app = Client()
        app.get("/chat/open", {"t": make_open_token(self.asha)})
        self.assertNotContains(app.get("/chat/"), "clearChat")

        agent = AppAccount.objects.create(name="Agent", email="g@example.org", admin_chat_mode=True)
        inbox = Client()
        inbox.get("/chat/open", {"t": make_open_token(agent)})
        page = inbox.get("/chat/inbox/")
        self.assertContains(page, 'id="clearChat"')
        self.assertContains(page, "/chat/inbox/0/clear")


class SupportInboxClearTests(TestCase):
    """A support agent (admin_chat_mode) clears a chat from the app's inbox, as the admin does."""

    def setUp(self):
        self.asha = AppAccount.objects.create(name="Asha", email="a@example.org")
        AppChatMessage.objects.create(account=self.asha, sender="user", body="Hello")
        AppChatMessage.objects.create(account=self.asha, sender="admin", body="Hi Asha", read_by_admin=True)
        AppNotificationLog.objects.create(source="chat", account=self.asha, title="New message from Admin", body="Hi Asha")
        AppNotificationLog.objects.create(source="admin", account=self.asha, title="Offer", body="Keep this")
        self.agent = AppAccount.objects.create(name="Agent", email="g@example.org", admin_chat_mode=True)
        self.inbox = self.opened(self.agent)

    def opened(self, account, **client_args):
        c = Client(**client_args)
        c.get("/chat/open", {"t": make_open_token(account)})
        return c

    def clear(self, client=None, account=None):
        return (client or self.inbox).post(f"/chat/inbox/{(account or self.asha).id}/clear")

    def test_an_agent_clears_every_message_and_the_chat_pushes(self):
        r = self.clear()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"ok": True})
        self.assertFalse(AppChatMessage.objects.filter(account=self.asha).exists())
        self.assertEqual(list(AppNotificationLog.objects.values_list("body", flat=True)), ["Keep this"])

    def test_gone_from_the_users_app_and_the_inbox_list(self):
        listed = lambda: [c["id"] for c in self.inbox.get("/chat/inbox/list.json").json()["conversations"]]
        self.assertIn(self.asha.id, listed())
        self.clear()
        self.assertNotIn(self.asha.id, listed())
        self.assertEqual(self.opened(self.asha).get("/chat/messages").json()["messages"], [])
        self.assertEqual(_chat_unread_count(self.asha), 0)

    def test_the_same_as_clearing_on_the_console(self):
        ravi = AppAccount.objects.create(name="Ravi", email="r@example.org")
        AppChatMessage.objects.create(account=ravi, sender="user", body="Mine")
        self.clear()
        self.assertEqual(ravi.chat_messages.count(), 1, "other people's chats stay")

    def test_a_plain_user_or_nobody_cannot(self):
        self.assertEqual(self.clear(client=self.opened(self.asha)).status_code, 401)
        self.assertEqual(self.clear(client=Client()).status_code, 401)
        self.assertEqual(self.asha.chat_messages.count(), 2)

    def test_an_agent_cannot_clear_their_own_chat(self):
        AppChatMessage.objects.create(account=self.agent, sender="user", body="My own")
        self.assertEqual(self.clear(account=self.agent).status_code, 403)
        self.assertEqual(self.agent.chat_messages.count(), 1)

    def test_a_plain_link_cannot_clear_it(self):
        self.assertEqual(self.inbox.get(f"/chat/inbox/{self.asha.id}/clear").status_code, 405)
        self.assertEqual(self.asha.chat_messages.count(), 2)

    def test_someone_who_does_not_exist(self):
        self.assertEqual(self.inbox.post("/chat/inbox/999999/clear").status_code, 404)

    def test_it_needs_the_pages_csrf_token(self):
        strict = self.opened(self.agent, enforce_csrf_checks=True)
        strict.get("/chat/inbox/")
        self.assertEqual(self.clear(client=strict).status_code, 403)
        self.assertEqual(self.asha.chat_messages.count(), 2)
        token = strict.cookies["csrftoken"].value
        r = strict.post(f"/chat/inbox/{self.asha.id}/clear", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(self.asha.chat_messages.exists())
