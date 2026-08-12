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
    """Harden SQLite against 'database is locked' on every new connection.

    WAL lets readers and one writer run concurrently (rollback-journal mode blocks readers
    while writing — the app's ~5s chat polling + admin + cron collide under it). busy_timeout
    makes a blocked writer wait instead of erroring immediately. Runs for SQLite only; a no-op
    on Postgres/MySQL if the DB is ever migrated.
    """
    if connection.vendor != "sqlite":
        return
    cursor = connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")       # persistent (stored in the DB header)
    cursor.execute("PRAGMA synchronous=NORMAL;")     # safe with WAL, much faster than FULL
    cursor.execute("PRAGMA busy_timeout=20000;")     # wait up to 20s for a lock before erroring
    cursor.execute("PRAGMA foreign_keys=ON;")        # enforce FK constraints (off by default)
