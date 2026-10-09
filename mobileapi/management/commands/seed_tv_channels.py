"""python manage.py seed_tv_channels — fill the TV station catalogue from iptv-org's public channel index.

Fetches channels.json / streams.json / logos.json / blocklist.json from iptv-org's API
(https://iptv-org.github.io/api/ — on PythonAnywhere's free-tier allowlist, both .github.io and
.githubusercontent.com are listed) and keeps only a curated set of categories (CATEGORIES below)
plus every India channel, excluding anything closed, NSFW, on iptv-org's own blocklist (copyright
takedowns), or with no known stream URL. No server-side stream health-check — thousands of channels
would burn the free-tier CPU-second quota for a result that's stale again the next day; a dead
stream just fails to play, same as any bad pasted link.

Safe to run again: an existing channel (same category + iptv-org id) gets its title/logo/stream
refreshed in place (these do drift over time) — nothing is deleted, and the admin's own on/off
switch (TV station admin page) is left untouched. New channels are added active (admin prunes
from there, same as Home shortcuts).
"""
import requests

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Max

from mobileapi.models import TvCategory, TvChannel

API_BASE = "https://iptv-org.github.io/api/"

# (display name, iptv-org category slug, iptv-org country code) — exactly one of slug/country is set.
CATEGORIES = [
    ("India", None, "IN"),
    ("News", "news", None),
    ("Sports", "sports", None),
    ("Movies", "movies", None),
    ("Entertainment", "entertainment", None),
    ("Music", "music", None),
    ("Kids", "kids", None),
    ("Documentary", "documentary", None),
    ("Science", "science", None),
    ("Comedy", "comedy", None),
]

DEFAULT_LIMIT = 60  # per category bucket
INDIA_LIMIT = 120  # the one country-specific bucket — more personally relevant, so a higher cap


def fetch_json(path):
    resp = requests.get(API_BASE + path, timeout=60, headers={"User-Agent": "SyncUp-backend/1"})
    resp.raise_for_status()
    return resp.json()


class Command(BaseCommand):
    help = "Fill the TV station catalogue from iptv-org's public channel index."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Only show what would change.")
        parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help=f"Max channels per category (default {DEFAULT_LIMIT}).")

    def handle(self, *args, **options):
        dry = options["dry_run"]
        limit = options["limit"]

        self.stdout.write("Fetching channels.json, streams.json, logos.json, blocklist.json from iptv-org…")
        try:
            channels = fetch_json("channels.json")
            streams = fetch_json("streams.json")
            logos = fetch_json("logos.json")
            blocklist = fetch_json("blocklist.json")
        except Exception as e:
            raise CommandError(f"Couldn't reach iptv-org: {e}")

        blocked = {b["channel"] for b in blocklist if b.get("channel")}

        stream_by_channel = {}
        for s in streams:
            cid = s.get("channel")
            if cid and s.get("url") and cid not in stream_by_channel:
                stream_by_channel[cid] = s["url"]

        logo_by_channel = {}
        for logo in logos:
            cid = logo.get("channel")
            if cid and logo.get("url") and (cid not in logo_by_channel or logo.get("in_use")):
                logo_by_channel[cid] = logo["url"]

        def eligible(ch):
            return (
                ch["id"] not in blocked
                and not ch.get("closed")
                and not ch.get("is_nsfw")
                and ch["id"] in stream_by_channel
            )

        candidates = [c for c in channels if eligible(c)]
        self.stdout.write(f"{len(candidates)} of {len(channels)} channels are eligible (not closed/NSFW/blocked, has a stream).")

        added = updated = 0
        new_cats = []
        with transaction.atomic():
            cat_pos = TvCategory.objects.aggregate(m=Max("position"))["m"] or 0
            cats_by_name = {c.name.lower(): c for c in TvCategory.objects.all()}

            for display_name, slug, country in CATEGORIES:
                if slug:
                    picked = [c for c in candidates if slug in (c.get("categories") or [])]
                else:
                    picked = [c for c in candidates if c.get("country") == country]
                picked.sort(key=lambda c: c["id"])
                picked = picked[: (INDIA_LIMIT if country else limit)]

                cat = cats_by_name.get(display_name.lower())
                if cat is None:
                    cat_pos += 1
                    new_cats.append(display_name)
                    if not dry:
                        cat = TvCategory.objects.create(name=display_name, position=cat_pos)
                        cats_by_name[display_name.lower()] = cat

                existing = {ch.source_id: ch for ch in cat.channels.all()} if cat else {}
                pos = (cat.channels.aggregate(m=Max("position"))["m"] or 0) if cat else 0
                cat_added = cat_updated = 0

                for ch in picked:
                    stream_url = stream_by_channel[ch["id"]]
                    logo_url = logo_by_channel.get(ch["id"], "")
                    title = ch.get("name") or ch["id"]
                    row = existing.get(ch["id"])
                    if row is None:
                        pos += 1
                        cat_added += 1
                        if not dry and cat is not None:
                            TvChannel.objects.create(
                                category=cat, source_id=ch["id"], title=title,
                                logo_url=logo_url, stream_url=stream_url,
                                country=ch.get("country", ""), position=pos,
                            )
                    elif row.title != title or row.logo_url != logo_url or row.stream_url != stream_url:
                        cat_updated += 1
                        if not dry:
                            row.title, row.logo_url, row.stream_url = title, logo_url, stream_url
                            row.save(update_fields=["title", "logo_url", "stream_url", "updated_at"])

                added += cat_added
                updated += cat_updated
                if cat_added or cat_updated:
                    self.stdout.write(f"  {display_name}: +{cat_added}, ~{cat_updated} ({len(picked)} candidates)")

        if not added and not updated:
            self.stdout.write("Nothing changed — the catalogue already matches iptv-org.")
            return
        what = f"{added} added, {updated} updated"
        if new_cats:
            what += f", {len(new_cats)} new categor{'ies' if len(new_cats) != 1 else 'y'} ({', '.join(new_cats)})"
        self.stdout.write(f"Would do: {what}. Nothing changed (dry run)." if dry else self.style.SUCCESS(what + "."))
