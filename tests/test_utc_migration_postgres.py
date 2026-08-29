"""DATA-H1-2: the TIMESTAMPTZ migration preserves every instant it moves.

The only Postgres-backed test in this suite, and it exists because nothing else
here *can* be. tests/conftest.py assigns DATABASE_URL to SQLite at import time,
so every other test -- including in CI, where the job sets a Postgres URL --
runs against SQLite. A migration that is a deliberate no-op on SQLite and does
all its work on Postgres is therefore invisible to the entire rest of the
suite, and CI's `alembic upgrade head` step only proves the DDL parses against
an EMPTY database. Neither notices timestamps being silently shifted.

WHY THE SESSION TIMEZONE IS SET TO SOMETHING WRONG
--------------------------------------------------
`ALTER COLUMN ... TYPE TIMESTAMPTZ` without an explicit `USING` clause reads
the existing naive values in the server's TimeZone setting. Under the default
UTC that is accidentally correct, so a test on a UTC server passes whether or
not the revision carries its USING clause -- it would assert nothing while
looking thorough. Running the migration under a deliberately non-UTC TimeZone
is what makes the clause's absence visible, and it catches all three of
H1-2's silent failure modes at once: a dropped USING on upgrade, a dropped
USING on downgrade, and a process_result_value that returns psycopg2's
session-local aware value without normalising it to UTC.

RUNNING IT
----------
Skipped unless TEST_POSTGRES_URL is set. CI sets it (.github/workflows/ci.yml).
Locally:

    docker compose up -d db
    TEST_POSTGRES_URL=postgresql://<user>:<pass>@localhost:5432/<db> pytest -q \
        tests/test_utc_migration_postgres.py

The test builds a uniquely-named schema and drops it again, so it never
touches anything already in the target database.
"""
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from alembic import command
from backend import migrations, models
from backend.enums import GroupKind

POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")

pytestmark = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="TEST_POSTGRES_URL is not set; see this module's docstring",
)

# The revision this one is chained from -- the state a database is in with rows
# already written as naive UTC, which is the only interesting starting point.
BEFORE = "a7226c349ecf"
REVISION = "e5f1b8d24a07"

# Deliberately not UTC, and deliberately not a whole number of hours away from
# it either: a half-hour offset makes an accidental pass on a rounded value
# impossible. India is UTC+05:30 with no DST, so the offset is the same in
# every season and this test cannot start failing in March.
WRONG_TIMEZONE = "Asia/Kolkata"

# Every column the revision moves, mirroring its COLUMNS tuple.
ALL_COLUMNS = (
    ("users", "last_seen"),
    ("equipment", "last_verified_at"),
    ("transaction_logs", "timestamp"),
    ("maintenance_logs", "opened_at"),
    ("maintenance_logs", "closed_at"),
    ("daily_stats", "date"),
    ("verifications", "created_date"),
    ("equipment_status_history", "created_date"),
)

# The instant every seeded row carries. NAIVE on purpose (hence the DTZ001
# waiver): it is written straight into a pre-migration TIMESTAMP WITHOUT TIME
# ZONE column by raw SQL, which is exactly the storage format every row already
# in a production database is in. Making it aware would seed a shape this
# migration will never actually meet.
SEEDED = datetime(2026, 6, 15, 9, 30, 0)  # noqa: DTZ001


@pytest.fixture()
def pg_schema():
    """An isolated schema on the target database, dropped when the test ends.

    search_path is set to this schema ALONE, not `<schema>, public`. CI runs
    `alembic upgrade head` against the same database before pytest, so `public`
    already carries a full schema and an alembic_version row; leaving public on
    the path would let Alembic find that row and conclude there is nothing to
    do. With only this schema visible it starts from base, as intended.
    """
    name = f"utc_mig_{uuid.uuid4().hex[:12]}"

    admin = create_engine(POSTGRES_URL)
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{name}"'))

    # search_path is set in the CONNECTION STRING, not by a 'connect' event
    # listener. The listener version of this fixture silently did nothing: the
    # event only fires for connections opened after it is registered, so the
    # pooled connection that CREATE SCHEMA had already opened came back without
    # it, every statement landed in `public`, and the tests appeared to pass
    # against CI's real schema instead of an isolated one. Putting it in the URL
    # makes it a property of every connection this engine can ever hand out.
    engine = create_engine(POSTGRES_URL, connect_args={"options": f"-csearch_path={name}"})

    try:
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{name}" CASCADE'))
        admin.dispose()


def _migrate(engine, revision, session_timezone=None, backwards=False):
    """Run Alembic to `revision`, optionally under a hostile session TimeZone.

    Same connection idiom as tests/test_group_schema.py's _upgrade/_downgrade
    and backend/migrations.run_migrations: Alembic joins the caller's
    transaction when handed a connection, so the alembic_version write needs
    begin()'s commit at exit.

    Direction is passed in rather than inferred from the target revision --
    BEFORE is both the upgrade target that sets up every test and the
    downgrade target of the last one, so there is no rule about the revision
    name that gets both right. command.upgrade asked to walk backwards does
    not fail; it finds no path forward and does nothing, which reads as a
    passing test until an assertion notices the schema never moved (the same
    trap tests/test_group_schema.py:59 documents).

    The SET runs on this very connection, which is the point -- it is the
    session the ALTER statements execute in.
    """
    cfg = migrations.alembic_config()
    with engine.begin() as conn:
        if session_timezone:
            conn.execute(text(f"SET TimeZone TO '{session_timezone}'"))
        cfg.attributes["connection"] = conn
        if backwards:
            command.downgrade(cfg, revision)
        else:
            command.upgrade(cfg, revision)


def _hostile_engine(engine):
    """An engine on the same schema whose every connection sits in WRONG_TIMEZONE.

    Both settings ride in connect_args so they hold for every connection the
    pool hands out, including the ones a Session opens on its own. A per-
    connection `SET` cannot make that promise, which is the bug the pg_schema
    fixture documents.

    The hostile zone is the entire point for the write path: a naive datetime
    reaching a TIMESTAMPTZ column is read AS the session zone, so under UTC a
    decorator that wrongly stripped tzinfo would be accidentally correct and
    the test would pass while asserting nothing.
    """
    with engine.connect() as conn:
        schema = conn.execute(text("SELECT current_schema()")).scalar_one()
    return create_engine(
        POSTGRES_URL,
        connect_args={"options": f"-csearch_path={schema} -cTimeZone={WRONG_TIMEZONE}"},
    )


def _column_types(engine):
    """Declared type names, keyed by (table, column), for THIS engine's schema.

    Asked of information_schema rather than SQLAlchemy's Inspector, which was
    wrong here twice over: it does not follow the libpq search_path set in
    connect_args (so it reflected `public` instead of the test schema), and its
    results are cached, so a second call after a migration returned the schema
    as it stood before the ALTER. Both failures point the same way -- they make
    a migration that worked look like one that did nothing -- and a plain query
    against the catalog has neither problem.
    """
    with engine.connect() as conn:
        schema = conn.execute(text("SELECT current_schema()")).scalar_one()
        rows = conn.execute(
            text(
                "SELECT table_name, column_name, data_type"
                " FROM information_schema.columns WHERE table_schema = :s"
            ),
            {"s": schema},
        ).all()

    found = {(t, c): d.upper() for t, c, d in rows}
    return {key: found[key] for key in ALL_COLUMNS if key in found}


def _seed(engine):
    """Exactly one row in every table that owns a UtcDateTime column, at SEEDED.

    All seven tables, not the two cheapest. An earlier version of this helper
    seeded `users` and `transaction_logs` only and left the other six columns
    proven by type change alone -- which establishes that a column was altered,
    not that the instant inside it survived, and the second is the claim the
    USING clause actually makes. The foreign-key chain here (catalog item ->
    group -> equipment -> verification) is the whole cost of closing that gap.

    Raw SQL rather than the ORM, deliberately: these rows stand in for data
    written before this migration existed, so they must NOT pass through the
    decorator the migration is changing. The ORM write path is a different
    claim and is tested separately, against an already-migrated schema.
    """
    with engine.begin() as conn:
        user_id = conn.execute(
            text(
                "INSERT INTO users (personal_number, password_hash, last_seen)"
                " VALUES ('h1_2_probe', 'x', :ts) RETURNING id"
            ),
            {"ts": SEEDED},
        ).scalar_one()

        catalog_id = conn.execute(
            text(
                "INSERT INTO catalog_items (name, category)"
                " VALUES ('H1-2 Probe Item', 'Test') RETURNING id"
            )
        ).scalar_one()

        group_id = conn.execute(
            text("INSERT INTO groups (name, kind) VALUES ('H1-2 Probe Group', :k) RETURNING id"),
            {"k": GroupKind.UNIT.value},
        ).scalar_one()

        equipment_id = conn.execute(
            text(
                "INSERT INTO equipment"
                " (serial_number, catalog_item_id, status, group_id, last_verified_at)"
                " VALUES ('H1_2_PROBE', :cat, 'Functional', :grp, :ts) RETURNING id"
            ),
            {"cat": catalog_id, "grp": group_id, "ts": SEEDED},
        ).scalar_one()

        conn.execute(
            text("INSERT INTO transaction_logs (event_type, timestamp) VALUES ('H1_2_PROBE', :ts)"),
            {"ts": SEEDED},
        )

        # Both of this table's timestamps at once, which is why closed_at is
        # given a value here rather than left NULL -- the NULL arm is its own
        # test below.
        conn.execute(
            text(
                "INSERT INTO maintenance_logs"
                " (equipment_id, description, status, opened_at, closed_at)"
                " VALUES (:eq, 'probe', 'Closed', :ts, :ts)"
            ),
            {"eq": equipment_id, "ts": SEEDED},
        )

        # "date" quoted: it is a type name, and this column is on the dead
        # DailyStats table (DATA-M18), which the revision converts anyway.
        conn.execute(
            text('INSERT INTO daily_stats ("date", total_items) VALUES (:ts, 1)'),
            {"ts": SEEDED},
        )

        verification_id = conn.execute(
            text(
                "INSERT INTO verifications"
                " (equipment_id, verification_type, reported_status, created_date, created_by)"
                " VALUES (:eq, 'ROUTINE', 'Functional', :ts, :usr) RETURNING id"
            ),
            {"eq": equipment_id, "ts": SEEDED, "usr": user_id},
        ).scalar_one()

        conn.execute(
            text(
                "INSERT INTO equipment_status_history"
                " (equipment_id, old_status, new_status, change_reason, verification_id,"
                "  created_date, created_by)"
                " VALUES (:eq, 'Functional', 'Broken', 'probe', :ver, :ts, :usr)"
            ),
            {"eq": equipment_id, "ver": verification_id, "ts": SEEDED, "usr": user_id},
        )


def _read_back_as_utc(engine):
    """The stored instants, rendered as naive UTC wall time, whatever the type.

    `AT TIME ZONE 'UTC'` is asymmetric and cannot be applied blindly: against a
    timestamptz it yields the UTC reading (what we want), but against a naive
    timestamp it ATTACHES a zone instead of resolving one, so calling it
    unconditionally reported a downgraded column as shifted by the session
    offset when the data was in fact intact. Casting to timestamptz first makes
    one expression correct for both states -- and the cast of an already-aware
    value is a no-op, so this reads the same instant either side of the
    migration.

    The READ session is pinned to UTC, unlike the migration itself. The cast
    above resolves a naive value using the session zone, so reading under
    WRONG_TIMEZONE would make this helper report the naive states shifted -- an
    artefact of the measurement, not of the data. The hostile timezone belongs
    on the connection that runs the ALTER (see _migrate), which is the session
    whose behaviour is actually under test.
    """
    with engine.connect() as conn:
        conn.execute(text("SET TimeZone TO 'UTC'"))
        # scalar_one(), not scalar(): _seed writes exactly one row per table, so
        # this also asserts the seed did not quietly double up or miss a table.
        return {
            (table, column): conn.execute(
                text(f'SELECT "{column}"::timestamptz AT TIME ZONE \'UTC\' FROM "{table}"')
            ).scalar_one()
            for table, column in ALL_COLUMNS
        }


# What _read_back_as_utc must return while the data is intact -- every column
# holding the one instant _seed wrote, whichever side of the migration we are on.
INTACT = {key: SEEDED for key in ALL_COLUMNS}


# --- the migration itself ----------------------------------------------------


def test_upgrade_preserves_the_instant_under_a_non_utc_session(pg_schema):
    """The USING clause's reason for existing, stated as an assertion.

    Delete `postgresql_using` from the revision and this test fails by exactly
    the WRONG_TIMEZONE offset -- which is the entire value of the file. A
    version of this test that ran under UTC would pass either way.
    """
    _migrate(pg_schema, BEFORE)
    _seed(pg_schema)

    before = _read_back_as_utc(pg_schema)
    assert before == INTACT

    _migrate(pg_schema, REVISION, session_timezone=WRONG_TIMEZONE)

    after = _read_back_as_utc(pg_schema)
    shifted = {k: (before[k], after[k]) for k in ALL_COLUMNS if before[k] != after[k]}
    assert shifted == {}, f"the migration shifted stored instants: {shifted}"


def test_every_column_becomes_timestamptz(pg_schema):
    """All eight columns move, not just the two the round-trip test seeds.

    Cheap to assert and worth asserting separately: a revision that dropped a
    line from its COLUMNS tuple would leave that column naive forever, and the
    test above only looks at two tables.
    """
    _migrate(pg_schema, BEFORE)

    before = _column_types(pg_schema)
    assert set(before) == set(ALL_COLUMNS), f"missing columns pre-migration: {before}"
    assert all(t == "TIMESTAMP WITHOUT TIME ZONE" for t in before.values()), before

    _migrate(pg_schema, REVISION, session_timezone=WRONG_TIMEZONE)

    after = _column_types(pg_schema)
    still_naive = {k: v for k, v in after.items() if v != "TIMESTAMP WITH TIME ZONE"}
    assert still_naive == {}, f"columns left as naive TIMESTAMP: {still_naive}"


def test_downgrade_restores_both_the_type_and_the_instant(pg_schema):
    """Reversible in a way this chain's other downgrades are not.

    c93f2a615d84 and a7226c349ecf both restore a shape without restoring data.
    This one restores both, and needs its own USING clause to do it -- a
    downgrade that dropped the clause would corrupt on the way back while the
    upgrade test stayed green.
    """
    _migrate(pg_schema, BEFORE)
    _seed(pg_schema)
    _migrate(pg_schema, REVISION, session_timezone=WRONG_TIMEZONE)

    _migrate(pg_schema, BEFORE, session_timezone=WRONG_TIMEZONE, backwards=True)

    assert _read_back_as_utc(pg_schema) == INTACT
    naive_again = _column_types(pg_schema)
    assert all(
        t == "TIMESTAMP WITHOUT TIME ZONE" for t in naive_again.values()
    ), naive_again


# --- the decorator, against a real TIMESTAMPTZ column ------------------------


def test_orm_reads_back_utc_even_when_the_session_is_not(pg_schema):
    """clock.process_result_value's astimezone arm, which SQLite cannot reach.

    psycopg2 returns a TIMESTAMPTZ in the session's timezone, so under
    WRONG_TIMEZONE the raw driver value is aware +05:30. Passing that through
    unchanged -- which the H1-1 implementation did -- would serialize as
    '+05:30' through Pydantic while clock.iso_z emits 'Z', splitting the single
    wire format H1-1 exists to establish. Nothing on SQLite can observe this:
    its DATETIME never returns an aware value at all.
    """
    from sqlalchemy.orm import sessionmaker

    from backend import models

    _migrate(pg_schema, REVISION)
    _seed(pg_schema)

    with pg_schema.connect() as conn:
        conn.execute(text(f"SET TimeZone TO '{WRONG_TIMEZONE}'"))

        # The driver's own view, to prove the session setting is really taking
        # effect -- otherwise this test could pass on a UTC session and assert
        # nothing about the normalisation it is named for.
        raw = conn.execute(
            text("SELECT timestamp FROM transaction_logs WHERE event_type = 'H1_2_PROBE'")
        ).scalar_one()
        assert raw.utcoffset() != timezone.utc.utcoffset(None), (
            f"session TimeZone did not take effect; raw value was {raw!r}"
        )

        session = sessionmaker(bind=conn)()
        log = (
            session.query(models.TransactionLog)
            .filter_by(event_type="H1_2_PROBE")
            .one()
        )
        assert log.timestamp.tzinfo is not None
        assert log.timestamp.utcoffset() == timezone.utc.utcoffset(None), (
            f"read back in the session's zone, not UTC: {log.timestamp!r}"
        )
        assert log.timestamp == SEEDED.replace(tzinfo=timezone.utc)
        session.close()


def test_orm_write_of_an_aware_value_stores_the_instant_not_the_wall_clock(pg_schema):
    """process_bind_param's Postgres arm, which nothing else reaches.

    Every other test here seeds with raw SQL -- on purpose, to stand in for
    pre-migration rows -- so until this test existed the WRITE half of the
    decorator's dialect split had no coverage on Postgres at all. That is one
    of the two silent failure modes clock.UtcDateTime's docstring names, and
    the more likely of the two to be reintroduced: `return value.replace(
    tzinfo=None)` for every dialect is exactly what H1-1 did.

    A value at +03:00 is written and must land as its UTC instant. Strip the
    zone instead of sending it and Postgres reads the naive result as
    WRONG_TIMEZONE, storing an instant 05:30 off.
    """
    _migrate(pg_schema, REVISION)
    hostile = _hostile_engine(pg_schema)
    try:
        plus_three = timezone(timedelta(hours=3))
        local_noon = datetime(2026, 6, 15, 12, 0, tzinfo=plus_three)  # == 09:00 UTC

        session = sessionmaker(bind=hostile)()
        session.add(models.TransactionLog(event_type="BIND_PROBE", timestamp=local_noon))
        session.commit()
        session.close()

        with hostile.connect() as conn:
            stored = conn.execute(
                text(
                    "SELECT timestamp AT TIME ZONE 'UTC' FROM transaction_logs"
                    " WHERE event_type = 'BIND_PROBE'"
                )
            ).scalar_one()
        assert stored == datetime(2026, 6, 15, 9, 0), (  # noqa: DTZ001
            f"the +03:00 offset was dropped rather than applied: stored {stored}"
        )
    finally:
        hostile.dispose()


def test_orm_write_of_a_naive_value_is_still_treated_as_utc(pg_schema):
    """The other half of the bind path: naive input is UTC by convention.

    H1-1 established that a naive datetime anywhere in this codebase means UTC.
    On Postgres that convention has to be applied EXPLICITLY before psycopg2
    sees the value -- an unstamped naive datetime handed to TIMESTAMPTZ is read
    as the session's local time, so dropping the `replace(tzinfo=utc)` line
    would silently reinterpret every such write as WRONG_TIMEZONE.
    """
    _migrate(pg_schema, REVISION)
    hostile = _hostile_engine(pg_schema)
    try:
        session = sessionmaker(bind=hostile)()
        session.add(models.TransactionLog(event_type="NAIVE_PROBE", timestamp=SEEDED))
        session.commit()
        session.close()

        with hostile.connect() as conn:
            stored = conn.execute(
                text(
                    "SELECT timestamp AT TIME ZONE 'UTC' FROM transaction_logs"
                    " WHERE event_type = 'NAIVE_PROBE'"
                )
            ).scalar_one()
        assert stored == SEEDED, f"naive input was read as local time, not UTC: {stored}"
    finally:
        hostile.dispose()


def test_null_survives_the_migration_and_both_directions_of_the_decorator(pg_schema):
    """closed_at is nullable, and None must stay None on the TIMESTAMPTZ arm.

    Cheap, but it is the one arm of process_bind_param/process_result_value
    that an ALTER over a table of NULLs could plausibly disturb, and the SQLite
    suite's None test cannot speak for the Postgres type.
    """
    _migrate(pg_schema, BEFORE)
    with pg_schema.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO maintenance_logs (description, status, opened_at, closed_at)"
                " VALUES ('null probe', 'Open', :ts, NULL)"
            ),
            {"ts": SEEDED},
        )

    _migrate(pg_schema, REVISION, session_timezone=WRONG_TIMEZONE)

    hostile = _hostile_engine(pg_schema)
    try:
        session = sessionmaker(bind=hostile)()
        ticket = session.query(models.MaintenanceLog).one()
        assert ticket.closed_at is None
        assert ticket.opened_at == SEEDED.replace(tzinfo=timezone.utc)

        # And a None written back through the decorator stays None.
        ticket.closed_at = None
        session.commit()
        assert session.query(models.MaintenanceLog).one().closed_at is None
        session.close()
    finally:
        hostile.dispose()


def test_create_all_agrees_with_the_migration_on_postgres(pg_schema):
    """Closes the Postgres half of a gap the existing schema test cannot see.

    test_group_schema.test_migration_and_create_all_build_the_same_schema
    compares migrated against create_all on SQLite, where both are DATETIME
    either way -- so it would stay green if load_dialect_impl and this revision
    disagreed about Postgres. The suite builds its schema with create_all and
    production migrates; that divergence is the thing worth pinning, and this
    is the only place it is visible.
    """
    from backend.database import Base

    _migrate(pg_schema, "head")
    migrated = _column_types(pg_schema)

    other = f"utc_mig_created_{uuid.uuid4().hex[:12]}"
    with pg_schema.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{other}"'))
    try:
        created_engine = create_engine(
            POSTGRES_URL, connect_args={"options": f"-csearch_path={other}"}
        )
        try:
            Base.metadata.create_all(bind=created_engine)
            created = _column_types(created_engine)
        finally:
            created_engine.dispose()
    finally:
        with pg_schema.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{other}" CASCADE'))

    assert created == migrated, (
        "create_all and the migration disagree about column types on Postgres: "
        f"{ {k: (created.get(k), migrated.get(k)) for k in set(created) | set(migrated) if created.get(k) != migrated.get(k)} }"
    )
