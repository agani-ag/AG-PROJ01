from django.core.management.base import BaseCommand
from django.db.models import Count, Max

from syncup.models import Location


class Command(BaseCommand):
    help = (
        "Collapse duplicate Location rows that share the same (device, latitude, "
        "longitude) — the same place stored on different dates — keeping the newest "
        "row (highest id) per place. Use --dry-run to preview."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be deleted without deleting anything.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        # Groups of (device, latitude, longitude) that have more than one row.
        dup_groups = (
            Location.objects
            .values("device_id", "latitude", "longitude")
            .annotate(n=Count("id"), keep=Max("id"))
            .filter(n__gt=1)
        )

        group_count = 0
        deleted_total = 0

        for grp in dup_groups.iterator():
            group_count += 1
            extras = Location.objects.filter(
                device_id=grp["device_id"],
                latitude=grp["latitude"],
                longitude=grp["longitude"],
            ).exclude(id=grp["keep"])
            n = extras.count()
            deleted_total += n
            if not dry_run and n:
                extras.delete()

        verb = "Would delete" if dry_run else "Deleted"
        self.stdout.write(self.style.SUCCESS(
            f"{verb} {deleted_total} duplicate location row(s) across {group_count} "
            f"(device, latitude, longitude) group(s)."
        ))
        if dry_run:
            self.stdout.write("Dry run only — no rows were deleted. Re-run without --dry-run to apply.")
