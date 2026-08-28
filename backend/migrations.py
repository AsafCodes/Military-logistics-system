"""
Schema management.

Replaces `Base.metadata.create_all`, which only ever created *missing tables*
and silently ignored columns added to existing ones. Every schema change now
goes through an Alembic revision in alembic/versions/.
"""
import os

from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from alembic import command

from . import models
from .database import create_database_engine

# Deliberately NOT database.engine. That one enforces foreign keys on SQLite,
# and Alembic's batch_alter_table rebuilds a table by dropping and renaming it,
# which fails under enforcement when anything references the table being
# rebuilt. The pragma cannot simply be turned off for the duration either:
# SQLite ignores it while a transaction is open, silently, and run_migrations()
# below runs inside engine.begin(). A connection that never had it set is the
# only arrangement that works. See database.create_database_engine.
engine = create_database_engine(enforce_foreign_keys=False)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Diff kinds that mean the database is BEHIND the models. Extra tables the
# models don't declare (compliance_logs, inventory_audits) are not drift --
# they are untracked legacy tables and must not block startup.
BEHIND = ("add_table", "add_column", "add_index")

# The last revision before H1-11 dropped the legacy hierarchy columns. A
# create_all database built any time before that revision landed is sitting on
# exactly this schema.
REVISION_BEFORE_LEGACY_DROP = "b1c4e7a90f52"

# H1-11's own revision -- the marker for a schema that has already lost the
# hierarchy columns but still carries Profile/UserRole, the shape H1-12 drops.
REVISION_BEFORE_PROFILE_DROP = "c93f2a615d84"


def alembic_config() -> Config:
    """Alembic config resolved from the project root, not the working directory.

    alembic.ini anchors script_location with %(here)s, which resolves against
    the ini file itself, so passing an absolute ini path is enough.
    """
    cfg = Config(os.path.join(PROJECT_ROOT, "alembic.ini"))
    cfg.attributes["configure_logger"] = False
    return cfg


def describe_drift(conn) -> list:
    """Return the diffs where the live schema is missing something the models declare."""
    context = MigrationContext.configure(conn)
    return [d for d in compare_metadata(context, models.Base.metadata) if d[0] in BEHIND]


def baseline_revision(inspector) -> str:
    """Which revision a pre-Alembic schema actually matches.

    A stamp is a claim about what the database already has, and the two ways
    of being wrong are not symmetric. Stamp too LATE and a revision re-runs
    against a schema that already carries its changes -- loud, and it fails at
    startup where someone will see it. Stamp too EARLY -- which "head" always
    is -- and every revision after the stamp is skipped in silence while the
    database reports itself current.

    This used to return "head" unconditionally. Nothing noticed for two
    revisions because both were additive and a create_all database already had
    what they add; H1-11 drops columns and tightens equipment.group_id, so a
    stamped database would have kept the columns, kept a nullable group_id, and
    still called itself up to date.

    Two shapes reach this function, and the legacy column is what tells them
    apart. A create_all database from before H1-11 still carries
    equipment.unit_hierarchy and needs that revision to run. One built after it
    never had the column, and is already at head -- stamping THAT at the older
    revision points H1-11's backfill at a column that is not there.

    Every destructive revision from here needs its own marker, or the databases
    it was written to change get stamped straight past it. H1-12 drops the
    profiles table, so a third shape now reaches this function: unit_hierarchy
    already gone but profiles still standing -- a database built between the
    two revisions landing. Caught by building exactly that shape and running
    run_migrations() against it rather than by reasoning about it: the first
    version of this function, which only knew the first two shapes, stamped it
    at "head" and silently skipped H1-12 while claiming to be current, which is
    the same class of bug this function exists to prevent.

    equipment.unit_hierarchy is the column read for the oldest shape, and not
    by coin toss: it is the one H1-11's backfill dereferences, so its presence
    is exactly the question of whether that revision can still run. Reading
    users.unit_hierarchy instead would answer identically for every schema
    that can actually exist -- both columns are dropped by the same revision,
    which mutation testing confirms as an equivalent mutant rather than a gap
    in coverage. H1-12 has no backfill to dereference anything, so `profiles`
    is read for its existence alone; `role`, `profile_id`, `battalion` and
    `company` would answer identically, since all five leave in the same
    revision -- another equivalent mutant, not a choice that matters.

    Called only after describe_drift() has passed, which is what guarantees
    the tables read below exist: a schema missing one is refused rather than
    stamped.
    """
    columns = {c["name"] for c in inspector.get_columns("equipment")}
    if "unit_hierarchy" in columns:
        return REVISION_BEFORE_LEGACY_DROP
    if inspector.has_table("profiles"):
        return REVISION_BEFORE_PROFILE_DROP
    return "head"


def run_migrations() -> None:
    """Bring the database up to head, baselining a pre-Alembic database if needed.

    A database created by the old create_all path has tables but no
    alembic_version, so `upgrade head` would fail trying to recreate them. If
    such a schema is missing nothing the models declare, it is stamped at the
    revision its schema actually matches (see baseline_revision). Note that
    only missing tables/columns/indexes are treated as
    drift: a column of the wrong type or nullability will still be stamped. If
    it is missing anything the models declare, we refuse rather than stamp --
    stamping a drifted database records a version it does not actually have,
    and the missing columns then fail at query time instead of at startup.
    """
    cfg = alembic_config()

    # begin(), not connect(): alembic runs inside the caller's transaction when
    # handed a connection, so the alembic_version write needs a commit at exit.
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        inspector = inspect(conn)

        # Gate on a model table, not on any table: a database holding only
        # untracked legacy tables is not a pre-Alembic schema, it is an empty one.
        if not inspector.has_table("alembic_version") and inspector.has_table("users"):
            drift = describe_drift(conn)
            if drift:
                raise RuntimeError(
                    "Refusing to baseline: this database predates Alembic and is "
                    "missing schema the models declare:\n  "
                    + "\n  ".join(str(d) for d in drift)
                    + "\n\nApply the missing schema by hand, then re-start; or, if the "
                    "data is expendable, drop the schema and let the migration build it."
                )
            command.stamp(cfg, baseline_revision(inspector))

        command.upgrade(cfg, "head")
