"""One-off repair for syncup's cleanup migration on SQLite. Run once, then delete this file.

`makemigrations` wrote RemoveField / RemoveConstraint / AlterUniqueTogether operations for models
that the same migration then deletes. On SQLite every one of those rebuilds the whole table, and
the rebuild of syncup_contact failed on its (device, phone_number) unique constraint:

    FieldDoesNotExist: NewContact has no field named 'device'

Those operations are pointless when the table is about to be dropped anyway. This rewrites the
pending migration to keep everything else and simply delete the models, children before parents,
so no table gets rebuilt at all. The original file is kept next to it as <name>.py.bak.

Usage, from the project root (the folder with manage.py):
    python fix_cleanup_migration.py
    python manage.py migrate
"""
import os
import shutil
import sys

sys.path.insert(0, os.getcwd())
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "main.settings")

import django  # noqa: E402

django.setup()

from django.db import connection  # noqa: E402
from django.db.migrations import operations as ops  # noqa: E402
from django.db.migrations.loader import MigrationLoader  # noqa: E402
from django.db.migrations.writer import MigrationWriter  # noqa: E402

APP = "syncup"
OPTION_OPS = tuple(getattr(ops, n) for n in (
    "AlterUniqueTogether", "AlterIndexTogether", "AlterModelOptions", "AlterModelTable",
    "AlterModelTableComment", "AlterOrderWithRespectTo", "AlterModelManagers") if hasattr(ops, n))


def fail(msg):
    print("NOT CHANGED: " + msg)
    sys.exit(1)


def target_model(op):
    """The (lower-case) model an operation acts on, or None."""
    if hasattr(op, "model_name_lower"):      # field, constraint and index operations
        return op.model_name_lower
    if isinstance(op, OPTION_OPS):
        return op.name_lower
    return None


def related_models(field):
    """Names of the syncup models a field points at (FK / one-to-one / many-to-many)."""
    remote = getattr(field, "remote_field", None)
    if remote is None:
        return set()
    model = remote.model
    if not isinstance(model, str):
        model = "%s.%s" % (model._meta.app_label, model._meta.model_name)
    if "." not in model:
        model = APP + "." + model
    app_label, name = model.lower().split(".", 1)
    return {name} if app_label == APP else set()


loader = MigrationLoader(connection)
pending = sorted(k for k in loader.disk_migrations if k[0] == APP and k not in loader.applied_migrations)
if len(pending) != 1:
    fail("expected exactly one unapplied %s migration, found %s" % (APP, [k[1] for k in pending] or "none"))
key = pending[0]
migration = loader.disk_migrations[key]

deletes = {op.name_lower: op for op in migration.operations if isinstance(op, ops.DeleteModel)}
if not deletes:
    fail("%s deletes no models, so there is nothing to fix" % key[1])

# Everything that isn't busywork on a table about to be dropped stays, in its original order.
others = [op for op in migration.operations
          if not isinstance(op, ops.DeleteModel) and target_model(op) not in deletes]
skipped = [op for op in migration.operations
           if not isinstance(op, ops.DeleteModel) and target_model(op) in deletes]

# Children before parents: a model is deleted once no model still waiting to be deleted points at it.
before = loader.project_state(key, at_end=False)
refs = {}
for name in deletes:
    state = before.models.get((APP, name))
    if state is None:
        fail("model %s.%s is not in the migration history" % (APP, name))
    fields = state.fields.values() if isinstance(state.fields, dict) else [f for _, f in state.fields]
    refs[name] = set().union(*(related_models(f) for f in fields)) & (set(deletes) - {name})
order, remaining = [], set(deletes)
while remaining:
    free = sorted(m for m in remaining if not any(m in refs[o] for o in remaining if o != m))
    if not free:
        fail("circular foreign keys between %s" % sorted(remaining))
    order += free
    remaining -= set(free)

migration.operations = others + [deletes[n] for n in order]
writer = MigrationWriter(migration)
path = writer.path
shutil.copyfile(path, path + ".bak")
with open(path, "w", encoding="utf-8") as fh:
    fh.write(writer.as_string())

print("Rewrote %s" % path)
print("  kept      %d operations (new tables, changes to models that stay)" % len(others))
print("  skipped   %d table rebuilds on models that are being deleted anyway" % len(skipped))
print("  deletes   %d models, children first: %s" % (len(order), ", ".join(order)))
print("  original  saved as %s.bak" % os.path.basename(path))
print("\nNext:  python manage.py migrate")
