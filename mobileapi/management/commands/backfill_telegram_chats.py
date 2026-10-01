"""Seed each partner's Telegram allow-list from the chats it has already sent to.

Partner sends are now restricted to PartnerTelegramChat entries, so run this ONCE after deploying
that change — otherwise partners already using /partner/v1/telegram/* start getting 403s.
Safe to run again: existing entries are left alone.

    python manage.py backfill_telegram_chats           # apply
    python manage.py backfill_telegram_chats --dry-run # just show what it would add
"""
from django.core.management.base import BaseCommand

from ...models import AppTelegramLog, PartnerTelegramChat


class Command(BaseCommand):
    help = "Allow each partner the Telegram chats it has successfully sent to before."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Show what would be added, change nothing.")

    def handle(self, *args, **options):
        pairs = (
            AppTelegramLog.objects
            .filter(partner__isnull=False, status="sent")
            .values_list("partner_id", "partner__name", "chat_id")
            .distinct()
        )
        added = skipped = 0
        for partner_id, partner_name, chat_id in pairs:
            chat_id = (chat_id or "").strip()
            if not chat_id:
                continue
            if PartnerTelegramChat.objects.filter(partner_id=partner_id, chat_id=chat_id).exists():
                skipped += 1
                continue
            if options["dry_run"]:
                self.stdout.write(f"would allow {partner_name} -> {chat_id}")
            else:
                PartnerTelegramChat.objects.create(
                    partner_id=partner_id, chat_id=chat_id, label="Added from send history",
                )
                self.stdout.write(f"allowed {partner_name} -> {chat_id}")
            added += 1
        verb = "would add" if options["dry_run"] else "added"
        self.stdout.write(self.style.SUCCESS(f"{verb} {added}, already present {skipped}"))
