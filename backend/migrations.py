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
from .database import engine

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Diff kinds that mean the database is BEHIND the models. Extra tables the
# models don't declare (compliance_logs, inventory_audits) are not drift --
# they are untracked legacy tables and must not block startup.
BEHIND = ("add_table", "add_column", "add_index")


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


def run_migrations() -> None:
    """Bring the database up to head, baselining a pre-Alembic database if needed.

    A database created by the old create_all path has tables but no
    alembic_version, so `upgrade head` would fail trying to recreate them. If
    such a schema already matches the models it is stamped as the baseline. If
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

        if not inspector.has_table("alembic_version") and inspector.get_table_names():
            drift = describe_drift(conn)
            if drift:
                raise RuntimeError(
                    "Refusing to baseline: this database predates Alembic and is "
                    "missing schema the models declare:\n  "
                    + "\n  ".join(str(d) for d in drift)
                    + "\n\nApply the missing schema by hand, then re-start; or, if the "
                    "data is expendable, drop the schema and let the migration build it."
                )
            command.stamp(cfg, "head")

        command.upgrade(cfg, "head")
