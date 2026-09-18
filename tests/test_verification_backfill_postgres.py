"""DATA-H5-2: the backfill picks the same evidence on Postgres as on SQLite.

tests/test_verification_backfill.py runs this revision against SQLite and
proves what it CHOOSES. This file exists because that cannot prove it RUNS
where it matters, and the difference is not theoretical: the obvious
single-statement spelling of this backfill -- MAX over a UNION ALL inside a
FROM-subquery correlating to equipment.id -- is accepted by SQLite and
rejected by Postgres, which requires LATERAL. The sibling of that trap is
GREATEST, which ignores NULL arguments on Postgres while SQLite's scalar
max() returns NULL if either argument is NULL, so a "simplification" of the
two statements into one could pass every SQLite test here and either fail
outright or quietly clear real timestamps in production.

CI's `alembic upgrade head` does not close this gap. It runs against a freshly
created database, so it proves the statements parse against an empty table and
nothing about what they do to rows -- the same argument
tests/test_utc_migration_postgres.py's docstring makes for its own existence.

RUNNING IT
----------
Skipped unless TEST_POSTGRES_URL is set. CI sets it
(.github/workflows/ci.yml). Locally:

    docker compose up -d db
    TEST_POSTGRES_URL=postgresql://<user>:<pass>@localhost:5432/<db> pytest -q \
        tests/test_verification_backfill_postgres.py

The pg_schema fixture it borrows builds a uniquely named schema and drops it
again, so it never touches anything already in the target database.
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from backend.enums import GroupKind
from tests.test_utc_migration_postgres import POSTGRES_URL, _migrate, pg_schema  # noqa: F401

pytestmark = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="TEST_POSTGRES_URL is not set; see this module's docstring",
)

BEFORE = "e5f1b8d24a07"
REVISION = "6f821fc450b8"

# AWARE, unlike the naive instants test_utc_migration_postgres seeds. That
# revision is the one that moves these columns to TIMESTAMPTZ and it seeds the
# shape from BEFORE the move; this one runs entirely after it, so the rows it
# meets are the ones the migrated schema holds.
FORGED = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
OLD_INSPECTION = datetime(2024, 3, 4, 8, 0, tzinfo=timezone.utc)
NEWER_INSPECTION = datetime(2025, 6, 7, 9, 0, tzinfo=timezone.utc)


def seed(engine, *, logged=None, log_event="VERIFICATION", filed=None):
    """One equipment row carrying a forged clock, plus whatever evidence is asked for.

    The foreign key chain is mandatory here and absent in the SQLite sibling:
    Postgres enforces these references, SQLite only parses them.
    """
    with engine.begin() as conn:
        catalog_id = conn.execute(text(
            "INSERT INTO catalog_items (name, category)"
            " VALUES ('H5 Probe Item', 'Test') RETURNING id"
        )).scalar_one()

        group_id = conn.execute(
            text("INSERT INTO groups (name, kind) VALUES ('H5 Probe Group', :k) RETURNING id"),
            {"k": GroupKind.UNIT.value},
        ).scalar_one()

        equipment_id = conn.execute(
            text(
                "INSERT INTO equipment"
                " (serial_number, catalog_item_id, status, group_id, last_verified_at)"
                " VALUES ('H5_PROBE', :cat, 'Functional', :grp, :ts) RETURNING id"
            ),
            {"cat": catalog_id, "grp": group_id, "ts": FORGED},
        ).scalar_one()

        if logged is not None:
            conn.execute(
                text(
                    "INSERT INTO transaction_logs (equipment_id, event_type, timestamp)"
                    " VALUES (:id, :event, :ts)"
                ),
                {"id": equipment_id, "event": log_event, "ts": logged},
            )

        if filed is not None:
            user_id = conn.execute(text(
                "INSERT INTO users (personal_number, password_hash)"
                " VALUES ('h5_probe', 'x') RETURNING id"
            )).scalar_one()
            conn.execute(
                text(
                    "INSERT INTO verifications (equipment_id, verification_type,"
                    " reported_status, created_by, created_date)"
                    " VALUES (:id, 'daily', 'Functional', :user, :created)"
                ),
                {"id": equipment_id, "user": user_id, "created": filed},
            )

    return equipment_id


def clock_of(engine, equipment_id):
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT last_verified_at FROM equipment WHERE id = :id"),
            {"id": equipment_id},
        ).scalar_one()


def test_the_backfill_runs_on_postgres_at_all(pg_schema):
    """The LATERAL trap, caught by running the statements against real rows.

    A correlated UNION ALL in a FROM-subquery raises here and passes on SQLite,
    so this assertion is mostly about the absence of an exception -- the value
    is checked too, since a statement that runs and picks wrong is the other
    half of the same mistake.
    """
    _migrate(pg_schema, BEFORE)
    equipment_id = seed(pg_schema, logged=OLD_INSPECTION)

    _migrate(pg_schema, REVISION)

    assert clock_of(pg_schema, equipment_id) == OLD_INSPECTION


def test_an_unearned_timestamp_is_cleared_on_postgres(pg_schema):
    """No evidence, so the forged instant goes. The unconditional first UPDATE."""
    _migrate(pg_schema, BEFORE)
    equipment_id = seed(pg_schema, logged=OLD_INSPECTION, log_event="HANDOVER")

    _migrate(pg_schema, REVISION)

    assert clock_of(pg_schema, equipment_id) is None


def test_the_later_evidence_wins_on_postgres(pg_schema):
    """The second UPDATE's comparison, on TIMESTAMPTZ rather than on ISO text.

    SQLite compares these as strings and Postgres as absolute instants, so the
    comparison that drives the overwrite is a genuinely different operation on
    each backend and is worth asserting on both.
    """
    _migrate(pg_schema, BEFORE)
    equipment_id = seed(pg_schema, logged=OLD_INSPECTION, filed=NEWER_INSPECTION)

    _migrate(pg_schema, REVISION)

    assert clock_of(pg_schema, equipment_id) == NEWER_INSPECTION
