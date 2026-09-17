from django.apps import AppConfig
from django.db.backends.signals import connection_created
from django.dispatch import receiver


class MobileapiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "mobileapi"
    verbose_name = "SyncUp Mobile API"

    def ready(self):
        # Register the SQLite hardening once the app is loaded.
        _ = _apply_sqlite_pragmas


@receiver(connection_created)
def _apply_sqlite_pragmas(sender, connection, **kwargs):
    """SQLite settings for every new connection.

    Rollback journal (DELETE), NOT WAL. WAL needs every process to share one memory-mapped index,
    which only works on a single machine. On PythonAnywhere the web app, Bash consoles and
    scheduled tasks run on different machines over a network filesystem, so under WAL a
    console's `migrate` (or a scheduled task's writes) went into the -wal file, the web app
    never saw them, and its own later writes overwrote them — the 0014 migration "applied OK"
    and then vanished. SQLite's docs: "WAL does not work over a network filesystem."

    journal_mode is stored in the DB file, so setting DELETE here also converts a database that
    is still in WAL the first time a connection has it to itself. busy_timeout makes a writer
    wait for a lock instead of failing with "database is locked". SQLite only; a no-op on
    Postgres/MySQL.
    """
    if connection.vendor != "sqlite":
        return
    cursor = connection.cursor()
    cursor.execute("PRAGMA journal_mode=DELETE;")    # rollback journal: safe on network filesystems
    cursor.execute("PRAGMA synchronous=FULL;")       # the durable setting for rollback-journal mode
    cursor.execute("PRAGMA busy_timeout=20000;")     # wait up to 20s for a lock before erroring
    cursor.execute("PRAGMA foreign_keys=ON;")        # enforce FK constraints (off by default)
