"""Prune accumulated waste data using the retention windows on AppConfig.

    python manage.py cleanup            # prune
    python manage.py cleanup --vacuum   # prune, then reclaim disk space
"""
from django.core.management.base import BaseCommand

from ...cleanup import run_cleanup


class Command(BaseCommand):
    help = "Delete expired/old rows (logs, tokens, sessions, chat, dead devices, finished reminders)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--vacuum", action="store_true",
            help="Run VACUUM after pruning to shrink the SQLite file (locks the DB briefly).",
        )

    def handle(self, *args, **options):
        stats = run_cleanup(vacuum=options["vacuum"])
        for key, value in stats.items():
            self.stdout.write(f"  {key}: {value}")
        total = sum(v for v in stats.values() if isinstance(v, int))
        self.stdout.write(self.style.SUCCESS(f"Cleanup done — {total} rows removed."))
