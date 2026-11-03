"""Channels: reading links, the one private list link, and sending links to chosen accounts."""
import json
import re
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from .channel_lists import ChannelLinkError, is_channel_link, list_text, parse_playlist, read_link, stream_hash
from .models import AppAccount, AppAuthToken, AppDevice, ChannelLink, ChannelSource, LiveChannel, SyncItem

LINK = "https://lists.example.org/index.m3u"
PLAYLIST = """#EXTM3U
#EXTINF:-1 tvg-id="One.in@SD" tvg-logo="https://img.example.org/1.png" group-title="News",News, One
https://s1.example.org/live.m3u8
#EXTINF:-1 tvg-id="Two.in@SD" group-title="Sports;News",Match Two
https://s2.example.org/live.m3u8
#EXTINF:-1 tvg-id="Three.in@SD" http-user-agent="Mozilla/5.0 (X11, Linux)" group-title="Music",Three With Header
https://s3.example.org/live.m3u8
#EXTINF:-1 tvg-id="Four.in@SD" group-title="Music",Four With Vlc Option
#EXTVLCOPT:http-referrer=https://example.org/
https://s4.example.org/live.m3u8
#EXTINF:-1 tvg-id="Five.in@SD" group-title="Kids",Five
http://s5.example.org/live.m3u8
#EXTINF:-1 group-title="Kids",Not A Web Stream
rtmp://s6.example.org/live
"""


class FakeResponse:
    def __init__(self, text, status=200):
        self.status_code = status
        self.text = text

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_content(self, _size):
        yield self.text.encode()


def reading(text, status=200):
    """read_link against a fake site; the language look-up stays off the network."""
    return patch("mobileapi.channel_lists.requests.get", return_value=FakeResponse(text, status))


class LinkTests(TestCase):
    def setUp(self):
        patcher = patch("mobileapi.channel_lists._languages", return_value={"One.in": "Tamil"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_channel_name_keeps_its_commas_and_headers_are_noticed(self):
        found = {c["url"].split("//")[1][:2]: c for c in parse_playlist(PLAYLIST)}
        self.assertEqual(found["s1"]["name"], "News, One")
        self.assertEqual(found["s3"]["name"], "Three With Header")
        self.assertTrue(found["s3"]["needs_headers"])
        self.assertTrue(found["s4"]["needs_headers"])
        self.assertFalse(found["s1"]["needs_headers"])
        self.assertNotIn("s6", found)

    def test_first_read_adds_new_channels_as_not_checked_and_leaves_out_sports_and_headers(self):
        with reading(PLAYLIST):
            result = read_link(LINK)
        self.assertEqual(result, {"found": 5, "added": 2, "already": 0, "sports": 1, "headers": 2})
        self.assertEqual(sorted(LiveChannel.objects.values_list("name", flat=True)), ["Five", "News, One"])
        self.assertEqual(set(LiveChannel.objects.values_list("status", flat=True)), {LiveChannel.NEW})
        self.assertEqual(LiveChannel.objects.get(name="News, One").language, "Tamil")
        self.assertEqual(ChannelSource.objects.get().url, LINK)

    def test_the_leave_outs_are_a_choice(self):
        with reading(PLAYLIST):
            result = read_link(LINK, leave_out_sports=False, leave_out_headers=False)
        self.assertEqual(result["added"], 5)
        self.assertEqual(LiveChannel.objects.count(), 5)

    def test_reading_a_link_again_adds_nothing_and_keeps_what_the_admin_decided(self):
        with reading(PLAYLIST):
            read_link(LINK)
            LiveChannel.objects.filter(name="Five").update(status=LiveChannel.REMOVED)
            LiveChannel.objects.filter(name="News, One").update(status=LiveChannel.OK)
            result = read_link(LINK)
        self.assertEqual(result["added"], 0)
        self.assertEqual(result["already"], 2)
        self.assertEqual(LiveChannel.objects.count(), 2)
        self.assertEqual(LiveChannel.objects.get(name="Five").status, LiveChannel.REMOVED)
        self.assertEqual(LiveChannel.objects.get(name="News, One").status, LiveChannel.OK)
        self.assertEqual(ChannelSource.objects.count(), 1)

    def test_the_same_stream_is_there_once_across_links_and_however_its_host_is_written(self):
        same = '#EXTM3U\n#EXTINF:-1,Copy\nhttps://S1.EXAMPLE.org/live.m3u8\n#EXTINF:-1,Copy Again\nhttps://s1.example.org/live.m3u8\n'
        with reading(PLAYLIST):
            read_link(LINK)
        with reading(same):
            result = read_link("https://other.example.org/x.m3u")
        self.assertEqual(result["added"], 0)
        self.assertEqual(LiveChannel.objects.filter(url_hash=stream_hash("https://s1.example.org/live.m3u8")).count(), 1)

    def test_what_is_not_a_channel_list_is_refused(self):
        cases = [
            ("<html>nope</html>", 200, "isn't a channel list"),
            ("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nv.m3u8\n", 200, "one stream"),
            ("#EXTM3U\n", 200, "no channels"),
            ("gone", 404, "404"),
        ]
        for text, status, words in cases:
            with self.subTest(words=words), reading(text, status), self.assertRaises(ChannelLinkError) as e:
                read_link(LINK)
            self.assertIn(words, str(e.exception))
        for bad in ("ftp://x.example.org/a.m3u", "javascript:alert(1)", "", None):
            with self.subTest(bad=bad), self.assertRaises(ChannelLinkError):
                read_link(bad)
        self.assertFalse(LiveChannel.objects.exists())

    def test_channel_links_are_told_by_their_address(self):
        for yes in ("https://a.example/x.m3u", "https://a.example/x.M3U8", "https://a.example/x.m3u?token=1", "http://a.example/p/x.m3u8#t"):
            self.assertTrue(is_channel_link(yes), yes)
        for no in ("https://a.example/x.mp4", "https://a.example/m3u", "https://a.example/?f=x.m3u", "", None, "http://[bad"):
            self.assertFalse(is_channel_link(no), no)


class SingleChannelTests(TestCase):
    URL = "https://cdn.example.org/live/one/index.m3u8"

    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("boss", "boss@example.org", "pw"))

    def add(self, url=URL, name="Channel One"):
        body = {"url": url, "name": name}
        return self.client.post(reverse("mobile_channels_read"), json.dumps(body), content_type="application/json")

    def test_a_link_and_a_name_add_one_channel_as_not_checked(self):
        self.assertEqual(self.add().status_code, 200)
        c = LiveChannel.objects.get()
        self.assertEqual((c.name, c.url, c.status, c.url_hash), ("Channel One", self.URL, LiveChannel.NEW, stream_hash(self.URL)))

    def test_the_server_never_fetches_it_so_a_stream_it_cannot_reach_still_goes_in(self):
        with patch("mobileapi.channel_lists.requests.get", side_effect=AssertionError("fetched")) as get:
            self.assertEqual(self.add().status_code, 200)
        get.assert_not_called()

    def test_it_reaches_the_private_list_only_once_marked_working(self):
        from .channel_lists import list_text
        self.add()
        self.assertNotIn(self.URL, list_text())
        LiveChannel.objects.update(status=LiveChannel.OK)
        self.assertIn(f',Channel One\n{self.URL}\n', list_text())

    def test_a_name_cannot_break_the_private_list(self):
        from .channel_lists import list_text
        self.add(name='Bad"\nName\n#EXTM3U')
        LiveChannel.objects.update(status=LiveChannel.OK)
        self.assertEqual(LiveChannel.objects.get().name, "Bad' Name #EXTM3U")
        self.assertEqual(len(list_text().splitlines()), 3)

    def test_the_same_link_twice_is_refused_and_says_which_one(self):
        self.add()
        r = self.add(self.URL.replace("cdn.example.org", "CDN.example.org"), "Again")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Channel One (Not checked)", r.json()["error"])
        self.assertEqual(LiveChannel.objects.count(), 1)

    def test_a_removed_channel_stays_removed(self):
        self.add()
        LiveChannel.objects.update(status=LiveChannel.REMOVED)
        self.assertIn("(Removed)", self.add(name="Again").json()["error"])
        self.assertEqual(LiveChannel.objects.get().status, LiveChannel.REMOVED)

    def test_only_web_links_are_accepted(self):
        for bad in ("ftp://x.example.org/a.m3u8", "javascript:alert(1)", "", "https://x.example.org/a b.m3u8", "https://x.example.org/a\n#EXTM3U"):
            with self.subTest(bad=bad):
                self.assertEqual(self.add(bad).status_code, 400)
        self.assertFalse(LiveChannel.objects.exists())

    def test_without_a_name_a_single_stream_says_how_to_add_it(self):
        with reading("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nv.m3u8\n"):
            r = self.add(name="")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Type its name", r.json()["error"])
        self.assertFalse(LiveChannel.objects.exists())

    def test_without_a_name_a_playlist_still_reads_as_before(self):
        with reading(PLAYLIST), patch("mobileapi.channel_lists._languages", return_value={}):
            r = self.add("https://list.example.org/all.m3u", "")
        self.assertEqual(r.status_code, 200)
        self.assertGreater(r.json()["result"]["added"], 1)

    def test_only_the_admin_can_add_one(self):
        self.client.logout()
        self.assertIn(self.add().status_code, (302, 403))
        self.assertFalse(LiveChannel.objects.exists())


class ListLinkTests(TestCase):
    def setUp(self):
        LiveChannel.objects.create(url_hash="a", url="https://s1.example.org/a.m3u8", name="Alpha", group="News", tvg_id="A.in@SD", logo="https://i/a.png", status=LiveChannel.OK)
        LiveChannel.objects.create(url_hash="b", url="https://s1.example.org/b.m3u8", name="Beta", status=LiveChannel.NEW)
        LiveChannel.objects.create(url_hash="c", url="https://s1.example.org/c.m3u8", name="Gamma", status=LiveChannel.REMOVED)
        self.link = ChannelLink.load()

    def url(self, token=None):
        return reverse("channel_list_m3u", args=[token or self.link.token])

    def test_the_link_gives_only_working_channels_as_an_m3u(self):
        r = self.client.get(self.url())
        self.assertEqual(r.status_code, 200)
        text = r.content.decode()
        self.assertTrue(text.startswith("#EXTM3U\n"))
        self.assertIn('tvg-id="A.in@SD" tvg-logo="https://i/a.png" group-title="News",Alpha\nhttps://s1.example.org/a.m3u8', text)
        self.assertNotIn("Beta", text)
        self.assertNotIn("Gamma", text)
        self.assertTrue(self.url().endswith(".m3u"))

    def test_a_wrong_link_finds_nothing(self):
        self.assertEqual(self.client.get(self.url("wrong")).status_code, 404)
        self.assertEqual(self.client.post(self.url()).status_code, 405)

    def test_a_listing_cannot_break_out_of_its_line(self):
        evil = '#EXTM3U\n#EXTINF:-1 group-title="x\\" tvg-logo=\\"y",Name "quoted"\nhttps://s9.example.org/l.m3u8\n'
        with reading(evil), patch("mobileapi.channel_lists._languages", return_value={}):
            read_link(LINK)
        LiveChannel.objects.filter(url_hash=stream_hash("https://s9.example.org/l.m3u8")).update(status=LiveChannel.OK)
        line = [l for l in list_text().splitlines() if "Name" in l][0]
        self.assertTrue(line.endswith(",Name 'quoted'"))
        self.assertEqual(line.count('"'), 6)


class AdminPageTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("boss", "boss@example.org", "pw"))
        for i in range(120):
            LiveChannel.objects.create(url_hash=f"h{i}", url=f"https://s.example.org/{i}.m3u8", name=f"Channel {i:03}", group="News" if i < 10 else "Music", language="Tamil" if i < 5 else "")
        self.ids = list(LiveChannel.objects.order_by("name").values_list("id", flat=True))

    def post(self, name, body):
        return self.client.post(reverse(name), json.dumps(body), content_type="application/json")

    def test_only_a_superuser_gets_in(self):
        self.client.logout()
        names = ["mobile_channels", "mobile_channels_rows", "mobile_channels_accounts"]
        posts = ["mobile_channels_mark", "mobile_channels_read", "mobile_channels_delete_link", "mobile_channels_new_link", "mobile_channels_send"]
        for name in names + posts:
            with self.subTest(name=name):
                self.assertIn(self.client.post(reverse(name)).status_code if name in posts else self.client.get(reverse(name)).status_code, (302, 403))
        self.assertEqual(LiveChannel.objects.filter(status=LiveChannel.OK).count(), 0)

    def test_the_page_opens_and_tv_station_is_gone(self):
        r = self.client.get(reverse("mobile_channels"))
        self.assertContains(r, "Add link")
        self.assertContains(r, ChannelLink.load().token)
        self.assertEqual(self.client.get("/services/tv-station").status_code, 404)

    def test_the_page_can_really_save_with_csrf_checks_on(self):
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(get_user_model().objects.get(username="boss"))
        page = strict.get(reverse("mobile_channels")).content.decode()
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page).group(1)
        body = json.dumps({"status": "ok", "ids": self.ids[:1]})
        self.assertEqual(strict.post(reverse("mobile_channels_mark"), body, content_type="application/json").status_code, 403)
        saved = strict.post(reverse("mobile_channels_mark"), body, content_type="application/json", headers={"X-CSRFToken": token})
        self.assertEqual(saved.status_code, 200)

    def test_rows_page_through_and_filter(self):
        data = self.client.get(reverse("mobile_channels_rows"), {"status": "new"}).json()
        self.assertEqual((data["total"], data["pages"], len(data["rows"])), (120, 3, 50))
        self.assertEqual(data["counts"], {"new": 120, "ok": 0, "removed": 0})
        last = self.client.get(reverse("mobile_channels_rows"), {"status": "new", "page": 3}).json()
        self.assertEqual(len(last["rows"]), 20)
        self.assertEqual(self.client.get(reverse("mobile_channels_rows"), {"status": "new", "group": "News"}).json()["total"], 10)
        self.assertEqual(self.client.get(reverse("mobile_channels_rows"), {"status": "new", "language": "Tamil"}).json()["total"], 5)
        self.assertEqual(self.client.get(reverse("mobile_channels_rows"), {"status": "new", "q": "channel 11"}).json()["total"], 10)
        self.assertEqual(self.client.get(reverse("mobile_channels_rows"), {"status": "ok"}).json()["total"], 0)

    def test_marking_channels_and_putting_them_back(self):
        r = self.post("mobile_channels_mark", {"status": "ok", "ids": self.ids[:3]}).json()
        self.assertEqual(r["changed"], 3)
        self.assertEqual(r["counts"], {"new": 117, "ok": 3, "removed": 0})
        self.assertIsNotNone(LiveChannel.objects.get(id=self.ids[0]).checked_at)
        self.post("mobile_channels_mark", {"status": "removed", "ids": self.ids[:1]})
        self.assertEqual(LiveChannel.objects.get(id=self.ids[0]).status, "removed")
        self.post("mobile_channels_mark", {"status": "new", "ids": self.ids[:1]})
        back = LiveChannel.objects.get(id=self.ids[0])
        self.assertEqual(back.status, "new")
        self.assertIsNone(back.checked_at)

    def test_a_removed_channel_can_be_played_and_go_straight_to_working(self):
        self.post("mobile_channels_mark", {"status": "removed", "ids": self.ids[:1]})
        row = self.client.get(reverse("mobile_channels_rows"), {"status": "removed"}).json()["rows"][0]
        self.assertEqual((row["id"], row["url"]), (self.ids[0], "https://s.example.org/0.m3u8"))
        r = self.post("mobile_channels_mark", {"status": "ok", "ids": self.ids[:1]}).json()
        self.assertEqual(r["counts"], {"new": 119, "ok": 1, "removed": 0})

    def test_all_matching_only_touches_what_the_filters_show(self):
        r = self.post("mobile_channels_mark", {"status": "ok", "all": True, "filters": {"status": "new", "group": "News"}}).json()
        self.assertEqual(r["changed"], 10)
        self.assertEqual(r["counts"]["ok"], 10)
        self.assertEqual(LiveChannel.objects.filter(status="ok").exclude(group="News").count(), 0)

    def test_bad_marks_change_nothing(self):
        self.assertEqual(self.post("mobile_channels_mark", {"status": "nope", "ids": self.ids}).status_code, 400)
        self.assertEqual(self.post("mobile_channels_mark", {"status": "ok"}).status_code, 400)
        self.assertEqual(self.post("mobile_channels_mark", {"status": "ok", "ids": ["1"]}).status_code, 400)
        self.assertEqual(LiveChannel.objects.filter(status="ok").count(), 0)

    def test_adding_a_link_reports_what_happened_and_a_bad_one_says_why(self):
        with reading(PLAYLIST), patch("mobileapi.channel_lists._languages", return_value={}):
            r = self.post("mobile_channels_read", {"url": LINK, "leave_out_sports": True, "leave_out_headers": True}).json()
        self.assertEqual(r["result"]["added"], 2)
        self.assertEqual(r["state"]["sources"][0]["url"], LINK)
        with reading("<html>"):
            bad = self.post("mobile_channels_read", {"url": LINK})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("isn't a channel list", bad.json()["error"])

    def test_deleting_a_link_leaves_its_channels_as_they_are_and_adding_it_back_undoes_nothing(self):
        with reading(PLAYLIST), patch("mobileapi.channel_lists._languages", return_value={}):
            self.post("mobile_channels_read", {"url": LINK})
        source = ChannelSource.objects.get()
        LiveChannel.objects.filter(name="Five").update(status=LiveChannel.REMOVED)
        LiveChannel.objects.filter(name="News, One").update(status=LiveChannel.OK)
        before = LiveChannel.objects.count()
        self.assertEqual(self.post("mobile_channels_delete_link", {"id": source.id}).json()["state"]["sources"], [])
        self.assertFalse(ChannelSource.objects.exists())
        self.assertEqual(LiveChannel.objects.count(), before)
        self.assertEqual(LiveChannel.objects.get(name="Five").status, LiveChannel.REMOVED)
        with reading(PLAYLIST), patch("mobileapi.channel_lists._languages", return_value={}):
            back = self.post("mobile_channels_read", {"url": LINK}).json()
        self.assertEqual(back["result"]["added"], 0)
        self.assertEqual(LiveChannel.objects.get(name="Five").status, LiveChannel.REMOVED)
        self.assertEqual(LiveChannel.objects.get(name="News, One").status, LiveChannel.OK)

    def test_a_bad_delete_changes_nothing(self):
        source = ChannelSource.objects.create(url=LINK)
        for bad in ("1", None, [source.id]):
            with self.subTest(bad=bad):
                self.assertEqual(self.post("mobile_channels_delete_link", {"id": bad}).status_code, 400)
        self.assertEqual(self.post("mobile_channels_delete_link", {"id": source.id + 1}).status_code, 200)
        self.assertTrue(ChannelSource.objects.filter(id=source.id).exists())

    def test_new_link_stops_the_old_one(self):
        old = ChannelLink.load().token
        fresh = self.post("mobile_channels_new_link", {}).json()["link"]
        self.assertEqual(self.client.get(reverse("channel_list_m3u", args=[old])).status_code, 404)
        self.assertEqual(self.client.get(fresh.replace("http://testserver", "")).status_code, 200)

    def test_accounts_can_be_searched(self):
        AppAccount.objects.create(name="Priya", email="priya@example.com")
        AppAccount.objects.create(name="Arun", phone="+919800000000")
        AppAccount.objects.create(name="Gone", email="gone@example.com", is_active=False)
        everyone = self.client.get(reverse("mobile_channels_accounts")).json()["accounts"]
        self.assertEqual([a["name"] for a in everyone], ["Arun", "Priya"])
        found = self.client.get(reverse("mobile_channels_accounts"), {"q": "priya@"}).json()["accounts"]
        self.assertEqual([a["detail"] for a in found], ["priya@example.com"])


class SendTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("boss", "boss@example.org", "pw"))
        self.a = AppAccount.objects.create(name="Priya", email="priya@example.com")
        self.b = AppAccount.objects.create(name="Arun", phone="+919800000000")
        AppDevice.objects.create(device_id="d1", account=self.a, fcm_token="tok-a", is_active=True)
        patcher = patch("mobileapi.channels_admin.fcm.push", side_effect=self.fake_push)
        self.push = patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def fake_push(tokens, *a, **k):
        return SimpleNamespace(status="sent" if tokens else "no_devices", delivered=len(tokens), failed=0)

    def send(self, **body):
        base = {"title": "Your list", "body": "Tap to add it.", "accounts": [self.a.id]}
        return self.client.post(reverse("mobile_channels_send"), json.dumps({**base, **body}), content_type="application/json")

    def test_my_list_goes_with_its_private_link_to_the_chosen_account_only(self):
        r = self.send(mode="list", name="SyncUp Channels")
        self.assertEqual(r.json(), {"delivered": 1, "failed": 0, "without_device": []})
        self.assertEqual(self.push.call_count, 1)
        args, kw = self.push.call_args
        self.assertEqual(args[0], ["tok-a"])
        self.assertEqual(kw["account"], self.a)
        self.assertEqual(kw["data"]["open_in"], "channels")
        self.assertEqual(kw["data"]["link_title"], "SyncUp Channels")
        self.assertTrue(kw["data"]["link_url"].endswith(ChannelLink.load().token + ".m3u"))

    def test_any_link_can_be_sent_and_named_after_its_site_when_blank(self):
        self.send(mode="any", link="http://lists.example.org/get.php?user=x")
        data = self.push.call_args.kwargs["data"]
        self.assertEqual(data["link_url"], "http://lists.example.org/get.php?user=x")
        self.assertEqual(data["link_title"], "lists.example.org")
        self.assertEqual(data["open_in"], "channels")

    def test_several_accounts_and_one_without_a_phone(self):
        r = self.send(mode="list", accounts=[self.a.id, self.b.id]).json()
        self.assertEqual(r["delivered"], 1)
        self.assertEqual(r["without_device"], ["Arun"])

    def test_bad_sends_send_nothing(self):
        for body in ({"mode": "any", "link": "javascript:alert(1)"}, {"mode": "any", "link": ""}, {"mode": "list", "accounts": []},
                     {"mode": "list", "title": ""}, {"mode": "list", "body": " "}, {"mode": "list", "accounts": ["1"]}):
            with self.subTest(body=body):
                self.assertEqual(self.send(**body).status_code, 400)
        self.assertEqual(self.push.call_count, 0)

    def test_no_firebase_on_the_server_is_reported(self):
        self.push.side_effect = lambda *a, **k: SimpleNamespace(status="not_configured", delivered=0, failed=0)
        r = self.send(mode="list")
        self.assertEqual(r.status_code, 503)


class PushPageTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("boss", "boss@example.org", "pw"))
        self.account = AppAccount.objects.create(name="Priya", email="priya@example.com")
        AppDevice.objects.create(device_id="d1", account=self.account, fcm_token="tok-a", is_active=True)

    def send(self, link, **extra):
        with patch("mobileapi.admin_views.fcm.push", return_value=SimpleNamespace(status="sent", delivered=1, failed=0)) as push:
            self.client.post(reverse("mobile_push"), {
                "audience": "account", "account": self.account.id, "title": "Title here", "body": "Body", "link_url": link, **extra,
            })
        return push.call_args.kwargs["data"]

    def test_a_pushed_channel_link_opens_channels_with_a_name(self):
        data = self.send("https://lists.example.org/index.m3u")
        self.assertEqual(data["open_in"], "channels")
        self.assertEqual(data["link_title"], "Title here")
        named = self.send("https://lists.example.org/x.m3u8?a=1", open_in="normal", link_title="My List")
        self.assertEqual((named["open_in"], named["link_title"]), ("channels", "My List"))

    def test_other_links_open_as_before(self):
        self.assertEqual(self.send("https://example.org/page", open_in="normal")["open_in"], "normal")
        self.assertEqual(self.send("https://example.org/page", open_in="syncup")["open_in"], "syncup")


class MissedPushTests(TestCase):
    """Whoever missed the push still finds it on the Notifications page, with what fills in Channels."""

    def setUp(self):
        self.client.force_login(get_user_model().objects.create_superuser("boss", "boss@example.org", "pw"))
        self.account = AppAccount.objects.create(name="Priya", email="priya@example.com")
        self.headers = {"HTTP_AUTHORIZATION": f"Bearer {AppAuthToken.issue(self.account, device_id='d1').key}"}

    def listed(self):
        self.client.post(reverse("mobile_channels_send"), json.dumps({
            "mode": "any", "link": "https://lists.example.org/index.m3u", "name": "My List",
            "title": "Your list", "body": "Tap to add it.", "accounts": [self.account.id],
        }), content_type="application/json")
        return self.client.get(reverse("app_notifications"), {"tab": "new"}, **self.headers).json()["items"]

    def assert_fills_in_channels(self, items):
        self.assertEqual(
            [(i["title"], i["link_url"], i["link_title"], i["open_in"]) for i in items],
            [("Your list", "https://lists.example.org/index.m3u", "My List", "channels")],
        )

    def test_a_delivered_push_is_listed_with_its_link(self):
        AppDevice.objects.create(device_id="d1", account=self.account, fcm_token="tok-a", is_active=True)
        with patch("mobileapi.fcm.is_configured", return_value=True), patch("mobileapi.fcm.send", return_value=(1, 0)) as send:
            items = self.listed()
        self.assertEqual(send.call_count, 1)
        self.assert_fills_in_channels(items)

    def test_a_push_that_never_reached_a_phone_is_still_listed(self):
        with patch("mobileapi.fcm.is_configured", return_value=True), patch("mobileapi.fcm.send") as send:
            items = self.listed()
        self.assertEqual(send.call_count, 0)
        self.assert_fills_in_channels(items)


class SavedListSyncTests(TestCase):
    """A person's saved channel lists travel with the account, through browser sync."""

    def test_saved_lists_sync_and_come_back_on_a_new_phone(self):
        account = AppAccount.objects.create(name="Priya", email="priya@example.com")
        headers = {"HTTP_AUTHORIZATION": f"Bearer {AppAuthToken.issue(account, device_id='d1').key}"}
        up = self.client.post(reverse("app_browser_sync"), json.dumps({
            "device_id": "d1", "since": "", "kinds": ["chanlist"],
            "changes": [{"kind": "chanlist", "key": "k1", "data": {"url": LINK, "title": "Mine"}, "updated_ms": 1000, "deleted": False}],
        }), content_type="application/json", **headers)
        self.assertEqual(up.status_code, 200)
        self.assertEqual(SyncItem.objects.get(account=account, kind="chanlist").data["title"], "Mine")
        # A new phone, or the same one after signing in again: an empty cursor brings it back.
        down = self.client.post(reverse("app_browser_sync"), json.dumps({"device_id": "d2", "since": "", "kinds": ["chanlist"], "changes": []}), content_type="application/json", **headers).json()
        self.assertEqual([(c["kind"], c["key"], c["data"]["title"]) for c in down["changes"]], [("chanlist", "k1", "Mine")])
