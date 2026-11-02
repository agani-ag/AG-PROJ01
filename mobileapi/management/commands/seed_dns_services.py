"""python manage.py seed_dns_services — fill the DNS list with a ready-made set of services.

Every one answered real lookups over HTTP/2 from India, as a phone asks them, on 2026-11-02, and what its
description says it blocks showed in its answers. All are https addresses: encrypted, and quick once
connected, where a DNS hostname opens a new connection for every lookup. No plain IP addresses, which aren't
encrypted. Keep to that when adding more, and check the services again from time to time: they can close.

Adds only what's missing: an address already on the list (any case) keeps its name, order and on/off. Safe
to run again — but a service the admin removed comes back, so after editing the list use --dry-run first to
see what it would add. Phones list new ones turned off, so nobody's browsing changes until they choose one.
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Max

from mobileapi.models import DnsService

SERVICES = [
    ("Cloudflare", "https://cloudflare-dns.com/dns-query", "Fast and private, blocks nothing"),
    ("Google", "https://dns.google/dns-query", "Fast and reliable, blocks nothing"),
    ("Control D Ad Block", "https://freedns.controld.com/p2", "Blocks ads and trackers"),
    ("AdGuard DNS", "https://dns.adguard-dns.com/dns-query", "Blocks ads and trackers"),
    ("Cloudflare Security", "https://security.cloudflare-dns.com/dns-query", "Blocks harmful sites"),
    ("Quad9", "https://dns.quad9.net/dns-query", "Blocks harmful sites"),
    ("Cloudflare for Families", "https://family.cloudflare-dns.com/dns-query", "Blocks adult and harmful sites, safe search on"),
    # These two also put YouTube in Restricted Mode, so some videos won't open.
    ("Control D Family", "https://freedns.controld.com/family", "Blocks adult sites and ads, safe search and restricted YouTube"),
    ("AdGuard Family", "https://family.adguard-dns.com/dns-query", "Blocks adult sites and ads, safe search and restricted YouTube"),
]


class Command(BaseCommand):
    help = "Add the ready-made DNS services that aren't on the list yet (never edits existing ones). Phones list them turned off."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Only show what would be added.")

    def handle(self, *args, **options):
        dry = options["dry_run"]
        have = {a.lower() for a in DnsService.objects.values_list("address", flat=True)}
        missing = [s for s in SERVICES if s[1].lower() not in have]
        if not missing:
            self.stdout.write(f"All {len(SERVICES)} ready-made DNS services are already there, nothing changed.")
            return
        for name, _, _ in missing:
            self.stdout.write(f"  {name}")
        what = f"{len(missing)} DNS service{'s' if len(missing) != 1 else ''}"
        if dry:
            self.stdout.write(f"Would add {what}. Nothing changed (dry run).")
            return
        with transaction.atomic():
            position = DnsService.objects.aggregate(m=Max("position"))["m"] or 0
            for position, (name, address, description) in enumerate(missing, start=position + 1):
                DnsService.objects.create(name=name, address=address, description=description, position=position)
        self.stdout.write(self.style.SUCCESS(f"Added {what}. They're on every phone, turned off."))
