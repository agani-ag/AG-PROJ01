"""Messages shows the same Active now / Last seen as the Devices page (from the person's phones), and the account
page's Message button opens that person's chat. A reply still skips the push only while their chat is open.
Run: python manage.py test mobileapi.tests_chat_presence"""
import json
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .chat_views import make_open_token
from .models import AppAccount, AppAuthToken, AppChatMessage, AppDevice


def ms(when):
    return int(when.timestamp() * 1000)


class PresenceTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("root", password="x"))
        self.now = timezone.now()
        self.asha = AppAccount.objects.create(name="Asha", email="a@example.org")
        AppAuthToken.issue(self.asha)
        AppChatMessage.objects.create(account=self.asha, sender="user", body="Hello")

    def phone(self, device_id, account=None, seen_ago=0, beat_ago=None):
        return AppDevice.objects.create(
            account=account or self.asha, device_id=device_id, fcm_token="t-" + device_id,
            last_seen=self.now - timedelta(seconds=seen_ago),
            active_at=None if beat_ago is None else self.now - timedelta(seconds=beat_ago),
        )

    def listed(self):
        row = self.client.get("/mobile/chats/list.json").json()["conversations"][0]
        return {"online": row["online"], "last_seen_ms": row["last_seen_ms"]}

    def everywhere(self):
        """The conversation list, the people picker and the chat's header: all must say the same."""
        people = self.client.get("/mobile/chats/people.json").json()["people"][0]
        header = self.client.get(f"/mobile/chats/{self.asha.id}/messages.json").json()["account"]
        return [self.listed()] + [{"online": x["online"], "last_seen_ms": x["last_seen_ms"]} for x in (people, header)]

    def test_active_now_when_syncup_is_open_on_their_phone_even_off_the_chat_screen(self):
        self.phone("p1", seen_ago=5, beat_ago=5)
        self.asha.chat_last_seen_at = self.now - timedelta(days=40)  # Chat itself not opened in weeks
        self.asha.save()
        self.assertEqual(self.everywhere(), [{"online": True, "last_seen_ms": ms(self.now - timedelta(seconds=5))}] * 3)

    def test_last_seen_is_their_phones_last_check_in_once_syncup_closes(self):
        self.phone("p1", seen_ago=3600, beat_ago=3600)
        self.phone("p2", seen_ago=600)  # the newer of two phones
        self.assertEqual(self.everywhere(), [{"online": False, "last_seen_ms": ms(self.now - timedelta(seconds=600))}] * 3)

    def test_the_chat_screen_alone_no_longer_makes_them_active(self):
        self.asha.chat_last_seen_at = self.now
        self.asha.save()
        self.assertEqual(self.everywhere(), [{"online": False, "last_seen_ms": 0}] * 3)

    def test_a_heartbeat_older_than_the_devices_window_is_not_active(self):
        self.phone("p1", seen_ago=80, beat_ago=80)
        self.assertFalse(self.listed()["online"])

    def test_someone_elses_phone_or_a_signed_out_one_does_not_count(self):
        other = AppAccount.objects.create(name="Bala", email="b@example.org")
        self.phone("theirs", account=other, beat_ago=1)
        AppDevice.objects.create(device_id="signed-out", fcm_token="x", active_at=self.now)
        self.assertEqual(self.listed(), {"online": False, "last_seen_ms": 0})

    def test_matches_the_devices_rule_exactly(self):
        d = self.phone("p1", seen_ago=20, beat_ago=20)
        self.assertEqual(self.listed()["online"], d.is_active_now)

    def test_the_support_agents_inbox_in_the_app_says_the_same(self):
        self.phone("p1", seen_ago=5, beat_ago=5)
        agent = AppAccount.objects.create(name="Agent", email="g@example.org", admin_chat_mode=True)
        inbox = Client()
        inbox.get("/chat/open", {"t": make_open_token(agent)})
        row = inbox.get("/chat/inbox/list.json").json()["conversations"][0]
        head = inbox.get(f"/chat/inbox/{self.asha.id}/messages.json").json()["account"]
        want = {"online": True, "last_seen_ms": ms(self.now - timedelta(seconds=5))}
        self.assertEqual([{k: x[k] for k in want} for x in (row, head)], [want, want])

    def send(self):
        with mock.patch("mobileapi.admin_views.fcm.push") as push:
            self.client.post(f"/mobile/chats/{self.asha.id}/reply", data=json.dumps({"body": "Hi"}), content_type="application/json")
        return push.called

    def test_a_reply_still_notifies_someone_using_another_part_of_syncup(self):
        self.phone("p1", beat_ago=1)
        self.assertTrue(self.send())

    def test_a_reply_skips_the_notification_while_their_chat_is_open(self):
        self.phone("p1", beat_ago=1)
        self.asha.chat_last_seen_at = timezone.now()
        self.asha.save()
        self.assertFalse(self.send())


class MessageButtonTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("root", password="x"))
        self.asha = AppAccount.objects.create(name="Asha", email="a@example.org")

    def account_page(self, account=None):
        return self.client.get(reverse("mobile_account_edit", args=[(account or self.asha).id])).content.decode()

    def start(self, value):
        page = self.client.get("/mobile/chats", {"with": value}).content.decode()
        return json.loads(page.split('<script id="start-chat" type="application/json">')[1].split("</script>")[0])

    def test_the_account_page_has_a_message_button_to_their_chat(self):
        self.assertIn(f'href="/mobile/chats?with={self.asha.id}"', self.account_page())

    def test_no_button_for_a_support_agent_or_a_disabled_account(self):
        agent = AppAccount.objects.create(name="Agent", email="g@example.org", admin_chat_mode=True)
        off = AppAccount.objects.create(name="Off", email="o@example.org", is_active=False)
        for a in (agent, off):
            with self.subTest(a.name):
                self.assertNotIn("/mobile/chats?with=", self.account_page(a))

    def test_messages_opens_that_chat_with_their_status(self):
        AppDevice.objects.create(account=self.asha, device_id="p1", fcm_token="t", active_at=timezone.now())
        self.assertEqual(self.start(self.asha.id), {
            "id": self.asha.id, "name": "Asha", "email": "a@example.org", "online": True,
            "last_seen_ms": ms(AppDevice.objects.get().last_seen),
        })

    def test_a_chat_can_be_opened_with_no_messages_yet_and_nothing_is_saved(self):
        self.assertEqual(self.start(self.asha.id)["name"], "Asha")
        self.assertFalse(AppChatMessage.objects.exists())

    def test_a_bad_or_missing_id_opens_nothing(self):
        for value in ("", "999999", "abc", "1;drop"):
            with self.subTest(value=value):
                self.assertIsNone(self.start(value))

    def test_a_name_cannot_break_out_of_the_page(self):
        AppAccount.objects.filter(pk=self.asha.pk).update(name='</script><script>alert(1)</script>')
        self.assertEqual(self.start(self.asha.id)["name"], '</script><script>alert(1)</script>')


class MenuCountsTests(TestCase):
    """The Messages menu: red = unread, green = people with SyncUp open now. Live via /mobile/chats/counts.json."""

    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("root", password="x"))

    def person(self, name, open_now=False, signed_in=True, **fields):
        a = AppAccount.objects.create(name=name, email=f"{name.lower()}@example.org", **fields)
        if signed_in:
            AppAuthToken.issue(a)
        AppDevice.objects.create(account=a, device_id=f"{name}-1", fcm_token="t",
                                 active_at=timezone.now() if open_now else None)
        return a

    def counts(self):
        return self.client.get("/mobile/chats/counts.json").json()

    def test_counts_unread_and_people_with_syncup_open(self):
        asha = self.person("Asha", open_now=True)
        self.person("Bala", open_now=True)
        self.person("Chitra")
        AppChatMessage.objects.create(account=asha, sender="user", body="One")
        AppChatMessage.objects.create(account=asha, sender="user", body="Two")
        AppChatMessage.objects.create(account=asha, sender="admin", body="Mine", read_by_admin=True)
        self.assertEqual(self.counts(), {"unread": 2, "active": 2})

    def test_opening_the_chat_drops_unread_straight_away(self):
        asha = self.person("Asha")
        AppChatMessage.objects.create(account=asha, sender="user", body="Hello")
        self.assertEqual(self.counts()["unread"], 1)
        self.client.get(f"/mobile/chats/{asha.id}/messages.json")
        self.assertEqual(self.counts()["unread"], 0)

    def test_active_counts_only_people_the_plus_list_offers_once_each(self):
        asha = self.person("Asha", open_now=True)
        AppDevice.objects.create(account=asha, device_id="Asha-2", fcm_token="t", active_at=timezone.now())  # two phones
        self.person("Agent", open_now=True, admin_chat_mode=True)
        self.person("Off", open_now=True, is_active=False)
        self.person("Out", open_now=True, signed_in=False)
        AppDevice.objects.create(device_id="nobody", fcm_token="t", active_at=timezone.now())  # signed-out install
        self.assertEqual(self.counts()["active"], 1)

    def test_only_the_admin_gets_the_counts(self):
        self.client.logout()
        self.assertIn(self.client.get("/mobile/chats/counts.json").status_code, (302, 403))

    def test_every_admin_page_shows_both_with_the_live_url(self):
        asha = self.person("Asha", open_now=True)
        AppChatMessage.objects.create(account=asha, sender="user", body="Hello")
        page = self.client.get(reverse("mobile_accounts")).content.decode()
        self.assertIn('data-counts="/mobile/chats/counts.json"', page)
        self.assertIn('data-count="active" title="With SyncUp open now">1<', page)
        self.assertIn('data-count="unread" title="Unread messages">1<', page)

    def test_a_zero_count_is_hidden(self):
        page = self.client.get(reverse("mobile_accounts")).content.decode()
        self.assertIn('data-count="active" title="With SyncUp open now" hidden>0<', page)
        self.assertIn('data-count="unread" title="Unread messages" hidden>0<', page)

    def test_the_plus_list_puts_active_people_first(self):
        self.person("Zara", open_now=True)
        self.person("asha")
        self.person("Bala", open_now=True)
        self.person("Chitra")
        d = self.client.get("/mobile/chats/people.json").json()
        self.assertEqual([(p["name"], p["online"]) for p in d["people"]],
                         [("Bala", True), ("Zara", True), ("asha", False), ("Chitra", False)])
        self.assertEqual((d["total"], d["active"]), (4, 2))
        found = self.client.get("/mobile/chats/people.json", {"q": "a"}).json()
        self.assertEqual([p["name"] for p in found["people"]][:2], ["Bala", "Zara"])
