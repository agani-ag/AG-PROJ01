"""DNS services: the address rules, the test lookup, the feed phones read, and the admin page."""
import json
import socket
import struct
import threading
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from . import dns_services
from .dns_services import HTTPS, PLAIN, TLS, kind_of, test_lookup
from .models import DnsService


class AddressTests(TestCase):
    def test_the_type_comes_from_what_was_typed(self):
        self.assertEqual(kind_of("https://dns.google/dns-query"), HTTPS)
        self.assertEqual(kind_of("dns.google"), TLS)
        self.assertEqual(kind_of("8.8.8.8"), PLAIN)
        self.assertEqual(kind_of("2001:4860:4860::8888"), PLAIN)

    def test_other_things_arent_addresses(self):
        for bad in ["", None, "not an address", "http://dns.google/dns-query", "https://dns.google", "999.1.1.1", "localhost", "1.2.3"]:
            self.assertIsNone(kind_of(bad), bad)


def _reply(query, rcode=0):
    return query[:2] + struct.pack("!H", 0x8000 | rcode) + query[4:]


class LookupTests(TestCase):
    """Real UDP round trips against a small server on this machine."""

    def _serve(self, answer):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("127.0.0.1", 0))
        self.addCleanup(s.close)
        port = s.getsockname()[1]

        def serve():
            data, peer = s.recvfrom(512)
            s.sendto(answer(data), peer)
        threading.Thread(target=serve, daemon=True).start()

        def ask(kind, address, query, timeout):
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as c:
                c.settimeout(timeout)
                c.sendto(query, ("127.0.0.1", port))
                return c.recv(4096)
        patcher = mock.patch.object(dns_services, "_ask", ask)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_real_reply_passes(self):
        self._serve(_reply)
        ms, problem = test_lookup("9.9.9.9")
        self.assertIsNone(problem)
        self.assertGreaterEqual(ms, 0)

    def test_no_such_name_still_counts_as_answering(self):
        self._serve(lambda q: _reply(q, 3))
        self.assertIsNone(test_lookup("9.9.9.9")[1])

    def test_a_server_failure_doesnt_pass(self):
        self._serve(lambda q: _reply(q, 2))
        self.assertIn("not with a DNS reply", test_lookup("9.9.9.9")[1])

    def test_rubbish_doesnt_pass(self):
        self._serve(lambda q: b"hello")
        self.assertIn("not with a DNS reply", test_lookup("9.9.9.9")[1])

    def test_no_answer_is_reported_not_raised(self):
        with mock.patch.object(dns_services, "_ask", side_effect=socket.timeout()):
            ms, problem = test_lookup("dns.google")
        self.assertIsNone(ms)
        self.assertIn("didn't answer", problem)

    def test_not_an_address(self):
        self.assertEqual(test_lookup("nonsense")[1], "That isn't a DNS address.")


class CatalogTests(TestCase):
    def _make(self, name, position=1, active=True):
        return DnsService.objects.create(name=name, address=f"https://{name.lower()}.example.com/dns-query", position=position, is_active=active, description="d")

    def test_open_to_anyone_in_order_without_hidden_ones(self):
        self._make("Second", 2)
        self._make("First", 1)
        self._make("Hidden", 3, active=False)
        r = self.client.get(reverse("app_dns"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual([s["name"] for s in r.json()["services"]], ["First", "Second"])
        self.assertEqual(set(r.json()["services"][0]), {"id", "name", "address", "description"})

    def test_an_empty_list_is_a_real_answer(self):
        self.assertEqual(self.client.get(reverse("app_dns")).json()["services"], [])

    def test_same_version_gets_304_and_a_change_doesnt(self):
        s = self._make("One")
        version = self.client.get(reverse("app_dns")).json()["version"]
        self.assertEqual(self.client.get(reverse("app_dns"), {"v": version}).status_code, 304)
        s.delete()
        self.assertEqual(self.client.get(reverse("app_dns"), {"v": version}).status_code, 200)

    def test_nothing_about_who_uses_what_is_kept(self):
        fields = {f.name for f in DnsService._meta.get_fields()}
        self.assertEqual(fields, {"id", "name", "address", "description", "position", "is_active", "created_at", "updated_at"})


class AdminTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser("dns-admin", "a@example.invalid", "x")
        self.client.force_login(self.admin)

    def _save(self, **body):
        return self.client.post(reverse("mobile_dns_save"), json.dumps(body), content_type="application/json")

    def test_everything_is_for_superusers_only(self):
        c = Client()
        c.force_login(get_user_model().objects.create_user("plain", "p@example.invalid", "x"))
        s = DnsService.objects.create(name="A", address="dns.google")
        for name, args in [("mobile_dns", []), ("mobile_dns_save", []), ("mobile_dns_test", []), ("mobile_dns_toggle", [s.id]), ("mobile_dns_move", [s.id]), ("mobile_dns_delete", [s.id])]:
            r = c.post(reverse(name, args=args), "{}", content_type="application/json")
            self.assertIn(r.status_code, (302, 403), name)
        self.assertTrue(DnsService.objects.filter(id=s.id, is_active=True).exists())

    def test_the_page_can_really_save_with_csrf_checks_on(self):
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.admin)
        self.assertContains(c.get(reverse("mobile_dns")), "csrfmiddlewaretoken")
        token = c.cookies["csrftoken"].value
        with mock.patch("mobileapi.admin_dns.test_lookup", return_value=(5, None)):
            r = c.post(reverse("mobile_dns_save"), json.dumps({"name": "G", "address": "dns.google"}), content_type="application/json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(DnsService.objects.filter(address="dns.google").exists())

    def test_the_page_shows_the_services_and_warns_on_plain_ones(self):
        DnsService.objects.create(name="Plain One", address="8.8.8.8")
        DnsService.objects.create(name="Safe One", address="https://dns.google/dns-query")
        page = self.client.get(reverse("mobile_dns"))
        self.assertContains(page, "Plain One")
        self.assertContains(page, "badge-warning", count=1)

    def test_the_sidebar_links_to_it(self):
        self.assertContains(self.client.get(reverse("mobile_dns")), "DNS services")

    def test_adding_tests_it_first(self):
        with mock.patch("mobileapi.admin_dns.test_lookup", return_value=(5, None)) as t:
            r = self._save(name="G", address="dns.google", description="Fast")
        t.assert_called_once_with("dns.google")
        self.assertEqual(r.json(), {"ok": True})
        s = DnsService.objects.get()
        self.assertEqual((s.name, s.address, s.description, s.is_active), ("G", "dns.google", "Fast", True))

    def test_one_that_doesnt_answer_waits_for_add_anyway(self):
        with mock.patch("mobileapi.admin_dns.test_lookup", return_value=(None, "It didn't answer.")):
            self.assertEqual(self._save(name="G", address="dns.google").json(), {"unanswered": "It didn't answer."})
            self.assertFalse(DnsService.objects.exists())
            self.assertEqual(self._save(name="G", address="dns.google", anyway=True).json(), {"ok": True})
        self.assertTrue(DnsService.objects.exists())

    def test_bad_input_is_refused(self):
        self.assertEqual(self._save(name="", address="dns.google").status_code, 400)
        self.assertEqual(self._save(name="G", address="nonsense").status_code, 400)
        DnsService.objects.create(name="G", address="dns.google")
        self.assertIn("already", self._save(name="H", address="DNS.GOOGLE").json()["error"])

    def test_new_ones_go_last(self):
        with mock.patch("mobileapi.admin_dns.test_lookup", return_value=(5, None)):
            self._save(name="B", address="b.example.com")
            self._save(name="A", address="a.example.com")
        self.assertEqual(list(DnsService.objects.values_list("name", flat=True)), ["B", "A"])

    def test_editing_keeps_it_and_only_retests_a_new_address(self):
        s = DnsService.objects.create(name="A", address="a.example.com", position=4)
        with mock.patch("mobileapi.admin_dns.test_lookup", return_value=(None, "no")) as t:
            self.assertEqual(self._save(id=s.id, name="Renamed", address="a.example.com").json(), {"ok": True})
            t.assert_not_called()
            self.assertEqual(self._save(id=s.id, name="Renamed", address="b.example.com").json(), {"unanswered": "no"})
        s.refresh_from_db()
        self.assertEqual((s.name, s.address, s.position), ("Renamed", "a.example.com", 4))

    def test_hide_and_show(self):
        s = DnsService.objects.create(name="A", address="dns.google")
        self.client.post(reverse("mobile_dns_toggle", args=[s.id]))
        self.assertEqual(self.client.get(reverse("app_dns")).json()["services"], [])
        self.client.post(reverse("mobile_dns_toggle", args=[s.id]))
        self.assertEqual(len(self.client.get(reverse("app_dns")).json()["services"]), 1)

    def test_reordering_stops_at_the_ends(self):
        a = DnsService.objects.create(name="A", address="a.example.com", position=1)
        b = DnsService.objects.create(name="B", address="b.example.com", position=2)
        c = DnsService.objects.create(name="C", address="c.example.com", position=3)

        def names():
            return list(DnsService.objects.values_list("name", flat=True))
        self.client.post(reverse("mobile_dns_move", args=[c.id]), {"to": "up"})
        self.assertEqual(names(), ["A", "C", "B"])
        self.client.post(reverse("mobile_dns_move", args=[a.id]), {"to": "down"})
        self.assertEqual(names(), ["C", "A", "B"])
        self.client.post(reverse("mobile_dns_move", args=[c.id]), {"to": "up"})
        self.assertEqual(names(), ["C", "A", "B"])
        self.client.post(reverse("mobile_dns_move", args=[b.id]), {"to": "down"})
        self.assertEqual(names(), ["C", "A", "B"])

    def test_removing(self):
        s = DnsService.objects.create(name="A", address="dns.google")
        self.client.post(reverse("mobile_dns_delete", args=[s.id]))
        self.assertFalse(DnsService.objects.exists())

    def test_the_test_button(self):
        with mock.patch("mobileapi.admin_dns.test_lookup", return_value=(12, None)):
            r = self.client.post(reverse("mobile_dns_test"), json.dumps({"address": "dns.google"}), content_type="application/json")
        self.assertEqual(r.json(), {"ms": 12, "problem": None})
