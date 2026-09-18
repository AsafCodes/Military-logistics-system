"""DATA-H5-2: the backfill keeps only timestamps an inspection actually earned.

b047d26 gave equipment.last_verified_at one writer, so nothing can forge a new
compliance timestamp. It repaired nothing already stored: every row still
carried whatever assign_owner, transfer_equipment or the old creation default
had written. This revision replaces each stored value with the latest evidence
of a real inspection -- a VERIFICATION or CONDITION_REPORT log row, or a
verifications row -- and clears the column where there is none.

These tests run the migration itself against a file-backed SQLite database,
using the harness tests/test_group_schema.py established: upgrade to the
revision BEFORE this one, insert rows by raw SQL as a live database would
already hold them, then upgrade to head and read the column back. Raw SQL on
both ends deliberately -- going through the ORM would test the model's
defaults rather than the migration's WHERE clauses.

The Postgres half of this lives in tests/test_verification_backfill_postgres.py,
because the statements here have a dialect trap that SQLite cannot see.
"""
import pytest
from sqlalchemy import create_engine, text

from backend import migrations
from backend.database import Base
from tests.test_group_schema import _downgrade, _upgrade

# The revision this one is chained from: the state a live database is in with
# forged timestamps already written.
BEFORE = "e5f1b8d24a07"
REVISION = "6f821fc450b8"

# Every timestamp below is naive ISO-8601, which is what clock.UtcDateTime
# stores on SQLite, and ordered so that a wrong pick is a visibly wrong year.
FORGED = "2026-09-01 12:00:00"
OLD_INSPECTION = "2024-03-04 08:00:00"
NEWER_INSPECTION = "2025-06-07 09:00:00"


@pytest.fixture
def db(tmp_path):
    """A database one revision behind, with a catalog item to hang equipment on."""
    engine = create_engine(f"sqlite:///{tmp_path / 'backfill.db'}")
    _upgrade(engine, BEFORE)
    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO groups (id, name, kind) VALUES (1, '188', 'unit')")
        conn.exec_driver_sql("INSERT INTO catalog_items (id, name) VALUES (1, 'M4')")
    yield engine
    engine.dispose()


def add_item(engine, item_id, serial, last_verified_at):
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO equipment (id, serial_number, catalog_item_id, group_id,"
                " last_verified_at) VALUES (:id, :serial, 1, 1, :verified)"
            ),
            {"id": item_id, "serial": serial, "verified": last_verified_at},
        )


def add_log(engine, item_id, event_type, timestamp):
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO transaction_logs (equipment_id, event_type, timestamp)"
                " VALUES (:id, :event, :ts)"
            ),
            {"id": item_id, "event": event_type, "ts": timestamp},
        )


def add_verification(engine, item_id, created_date):
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO verifications (equipment_id, verification_type,"
                " reported_status, created_by, created_date)"
                " VALUES (:id, 'daily', 'Functional', 1, :created)"
            ),
            {"id": item_id, "created": created_date},
        )


def clocks(engine):
    with engine.connect() as conn:
        return dict(
            conn.execute(text(
                "SELECT serial_number, last_verified_at FROM equipment ORDER BY id"
            )).all()
        )


def test_a_timestamp_with_no_evidence_behind_it_is_cleared(db):
    """The headline: paperwork wrote it, so it says nothing and must not stand."""
    add_item(db, 1, "FORGED", FORGED)

    _upgrade(db, REVISION)

    assert clocks(db) == {"FORGED": None}


@pytest.mark.parametrize("event_type", ["HANDOVER", "HANDOVER_LOC", "ASSIGN", "FIX", "CREATE"])
def test_paperwork_events_are_not_evidence(db, event_type):
    """Every event that is not somebody looking at the item, one per case.

    A guard written as "has any log row" would pass on all five of these and
    preserve exactly the forged timestamps this revision exists to remove.
    """
    add_item(db, 1, "PAPER", FORGED)
    add_log(db, 1, event_type, FORGED)

    _upgrade(db, REVISION)

    assert clocks(db) == {"PAPER": None}


@pytest.mark.parametrize("event_type", ["VERIFICATION", "CONDITION_REPORT"])
def test_a_logged_inspection_lowers_the_clock_to_when_it_happened(db, event_type):
    """The stored value outran its evidence, which is what a transfer did."""
    add_item(db, 1, "CHECKED", FORGED)
    add_log(db, 1, event_type, OLD_INSPECTION)

    _upgrade(db, REVISION)

    assert clocks(db) == {"CHECKED": OLD_INSPECTION}


def test_a_filed_verification_counts_even_with_no_log_row(db):
    """The years when create_verification wrote no log at all (before DATA-H4-3)."""
    add_item(db, 1, "FILED", FORGED)
    add_verification(db, 1, OLD_INSPECTION)

    _upgrade(db, REVISION)

    assert clocks(db) == {"FILED": OLD_INSPECTION}


@pytest.mark.parametrize(
    "log_at,filed_at",
    [(OLD_INSPECTION, NEWER_INSPECTION), (NEWER_INSPECTION, OLD_INSPECTION)],
    ids=["filed_is_later", "logged_is_later"],
)
def test_the_latest_evidence_wins_whichever_kind_it_is(db, log_at, filed_at):
    """Both orders, because the second statement only overwrites conditionally.

    Dropping its WHERE would always prefer the filed verification and silently
    discard a later daily check; inverting the comparison would always prefer
    the log.
    """
    add_item(db, 1, "BOTH", FORGED)
    add_log(db, 1, "VERIFICATION", log_at)
    add_verification(db, 1, filed_at)

    _upgrade(db, REVISION)

    assert clocks(db) == {"BOTH": max(log_at, filed_at)}


def test_the_latest_of_several_inspections_wins(db):
    """MAX, not "the first one found" -- a real item is checked repeatedly."""
    add_item(db, 1, "REPEAT", FORGED)
    for stamp in (OLD_INSPECTION, NEWER_INSPECTION, "2024-11-11 11:00:00"):
        add_log(db, 1, "VERIFICATION", stamp)

    _upgrade(db, REVISION)

    assert clocks(db) == {"REPEAT": NEWER_INSPECTION}


def test_an_already_null_clock_stays_null(db):
    """Items created since b047d26. Nothing to repair, and nothing invented."""
    add_item(db, 1, "NEVER", None)

    _upgrade(db, REVISION)

    assert clocks(db) == {"NEVER": None}


def test_evidence_belongs_to_one_item_only(db):
    """The correlation is per row. Without it one inspection greens the fleet.

    This is the bug the revision is repairing, reintroduced by a missing
    predicate -- so it gets the same fleet-wide shape as the behaviour tests in
    tests/test_verification_clock.py.
    """
    add_item(db, 1, "CHECKED", FORGED)
    add_item(db, 2, "UNCHECKED", FORGED)
    add_item(db, 3, "ALSO-UNCHECKED", FORGED)
    add_log(db, 1, "VERIFICATION", OLD_INSPECTION)
    add_verification(db, 1, NEWER_INSPECTION)

    _upgrade(db, REVISION)

    assert clocks(db) == {
        "CHECKED": NEWER_INSPECTION,
        "UNCHECKED": None,
        "ALSO-UNCHECKED": None,
    }


def test_a_log_row_belonging_to_no_item_is_not_evidence_for_any_item(db):
    """transaction_logs.equipment_id is nullable, so a NULL row is reachable.

    No route writes one today -- record_event refuses an equipment with no id
    rather than writing an unreachable row -- and none did historically either:
    before DATA-H4-3 create_equipment wrote no log at all. What the column's
    nullability leaves open is everything that does not go through a route: a
    hand-written import, a psql session, a fixture. NULL matches no id, so such
    a row is invisible to the correlation rather than evidence for every item,
    which is the answer worth pinning either way.
    """
    add_item(db, 1, "ITEM", FORGED)
    with db.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO transaction_logs (equipment_id, event_type, timestamp)"
            f" VALUES (NULL, 'VERIFICATION', '{OLD_INSPECTION}')"
        )

    _upgrade(db, REVISION)

    assert clocks(db) == {"ITEM": None}


def test_an_inspection_with_no_time_on_it_dates_nothing(db):
    """A log row whose own timestamp is NULL says an inspection happened and not
    when, which cannot date a compliance clock. MAX ignores it and the item is
    cleared -- the honest answer rather than the row's own creation time.
    """
    add_item(db, 1, "UNDATED", FORGED)
    with db.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO transaction_logs (equipment_id, event_type, timestamp)"
            " VALUES (1, 'VERIFICATION', NULL)"
        )

    _upgrade(db, REVISION)

    assert clocks(db) == {"UNDATED": None}


def test_evidence_later_than_the_stored_value_still_wins(db):
    """The backfill follows the evidence, in both directions.

    Usually it lowers a clock, because paperwork ran after the last real check.
    The other order exists too -- a verification whose stamp never committed,
    or clock skew between rows -- and the rule is the same either way: the
    column reports the last inspection on record. Pinned because "only ever
    lowers" is the easy thing to assume and would justify a WHERE that breaks
    this case.
    """
    add_item(db, 1, "SKEWED", OLD_INSPECTION)
    add_log(db, 1, "VERIFICATION", NEWER_INSPECTION)

    _upgrade(db, REVISION)

    assert clocks(db) == {"SKEWED": NEWER_INSPECTION}


def test_running_the_backfill_again_changes_nothing(db):
    """Operators re-run migrations, and a downgrade puts this one back in reach.

    The second pass reads the same evidence and must land on the same instant.
    A backfill that walked the clock -- toward now, or toward NULL -- would
    corrupt quietly on the second run, when nobody is watching the first.
    """
    add_item(db, 1, "REPAIRED", FORGED)
    add_log(db, 1, "VERIFICATION", OLD_INSPECTION)

    _upgrade(db, REVISION)
    once = clocks(db)
    _downgrade(db, BEFORE)
    _upgrade(db, REVISION)

    assert clocks(db) == once == {"REPAIRED": OLD_INSPECTION}


def test_the_backfill_runs_on_a_database_with_no_equipment_at_all(db):
    """CI upgrades an empty database, so the statements must survive zero rows."""
    _upgrade(db, REVISION)

    assert clocks(db) == {}


def test_a_pre_alembic_database_is_repaired_rather_than_stamped_past(tmp_path, monkeypatch):
    """The databases this revision was written for are the ones that never migrated.

    A create_all database carrying years of forged timestamps has tables and no
    alembic_version, so run_migrations baselines it. Until DATA-H5-2 that
    baseline was "head", which records every revision as applied -- including
    this one, which had never run. The result is the worst available outcome:
    the database calls itself repaired, keeps reporting compliance nobody
    earned, and no later migration will ever revisit it.

    Its schema cannot betray the difference, because a data-only revision
    leaves no schema behind, so this is asserted end to end through
    run_migrations() rather than against baseline_revision's return value.
    H1-11 and H1-12 each fixed this same silent skip for their own revision;
    tests/test_group_schema.py holds those.
    """
    path = tmp_path / "legacy_create_all.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO groups (id, name, kind) VALUES (1, '188', 'unit')")
        conn.exec_driver_sql("INSERT INTO catalog_items (id, name) VALUES (1, 'M4')")
    add_item(engine, 1, "FORGED", FORGED)
    add_item(engine, 2, "CHECKED", FORGED)
    add_log(engine, 2, "VERIFICATION", OLD_INSPECTION)
    engine.dispose()

    engine = create_engine(f"sqlite:///{path}")
    monkeypatch.setattr("backend.migrations.engine", engine)
    migrations.run_migrations()

    assert clocks(engine) == {"FORGED": None, "CHECKED": OLD_INSPECTION}, (
        "the backfill was stamped past, so a legacy database kept its forged "
        "compliance timestamps and now reports itself fully migrated"
    )
    engine.dispose()


def test_the_downgrade_runs_and_restores_nothing(db):
    """Reversible for schema purposes, honest about the data.

    The forged values are gone the moment upgrade() runs. Pinned because a
    downgrade that quietly restored them would restore the defect, and because
    an irreversible revision that RAISES would strand the chain.
    """
    add_item(db, 1, "FORGED", FORGED)
    _upgrade(db, REVISION)

    _downgrade(db, BEFORE)

    assert clocks(db) == {"FORGED": None}
