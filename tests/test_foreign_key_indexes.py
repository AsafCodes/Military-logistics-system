"""
DATA-H13-1 -- the indexes on the referencing side of every foreign key.

tests/test_group_schema.py already holds the two structural promises: every
foreign key column the models declare is covered by an index, and a migrated
schema matches a create_all one. This file holds what those cannot see.

THAT THE INDEXES A QUERY NEEDS ARE THE ONES IT USES. The metadata can say a
column is indexed and cannot say anything reads the index. The first half sends
real requests, captures the SQL each route emits, and asks SQLite how it would
run it. That is a claim about SQLite's planner and no other: Postgres chooses
from its own statistics and is not exercised here.

Only the indexes a route reads are covered that way, and they are the minority.
The rest exist for parent-row deletes, which no route performs yet; the
revision's docstring says which are which. Those are held by the structural
tests alone.

THAT THE REVISION SURVIVES THE STATES IT WILL MEET. The second half runs the
upgrade against a database with none of its indexes, with all but one, and
with something else under one of their names, and runs the downgrade against
one with all of them and one with all but one. The remaining state -- the
upgrade meeting every index already in place -- is held by the re-run test in
tests/test_group_schema.py, which covers every allowlisted revision at once.
"""
import re
from datetime import timedelta

import pytest
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event, inspect, text

from backend import clock, migrations, models
from tests.conftest import create_auth_header, recorded
from tests.test_group_schema import (
    _downgrade,
    _rerun_upgrade,
    _schema_snapshot,
    _upgrade,
)

INDEX_REVISION = "f4d81b2c6a93"
# What the chain looked like the moment before this revision: every table and
# constraint of today, and none of these indexes.
PARENT_REVISION = "d3a9c17be540"

# The migration tests below that downgrade, or compare a schema, stop at
# INDEX_REVISION rather than at head. The revision after this one (DATA-H13-2)
# has a downgrade that deliberately does not restore everything it changed, so
# a round trip through head would be measuring that revision, not this one.
# The pre-Alembic test goes to head on purpose: run_migrations() always does.


def _script():
    return ScriptDirectory.from_config(migrations.alembic_config())


def _expected_indexes():
    """name -> (table, column), read off the revision rather than retyped.

    A second list here would be free to disagree with the first, and the
    revision is the one that ships.
    """
    module = _script().get_revision(INDEX_REVISION).module
    return {f"ix_{table}_{column}": (table, column) for table, column in module.INDEXES}


def _indexes(engine):
    """name -> (table, columns, unique) for every index in the database."""
    insp = inspect(engine)
    return {
        index["name"]: (table, tuple(index["column_names"]), bool(index["unique"]))
        for table in insp.get_table_names()
        for index in insp.get_indexes(table)
    }


# --- the indexes are used ---------------------------------------------------

def _plans(client, db_session, who, method, path, table):
    """The query plan of each SELECT against `table` that one request emits.

    Captured from the route rather than rebuilt here: a query retyped in a test
    is planned exactly as the test wrote it, whatever the route goes on to do.
    """
    bind = db_session.get_bind()
    touches = re.compile(rf"\b(FROM|JOIN) {table}\b")
    seen = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT") and touches.search(statement):
            seen.append((statement, parameters))

    # A warm identity map answers without going to the database at all -- see
    # conftest.count_queries, which exists because of it.
    db_session.expire_all()
    event.listen(bind, "before_cursor_execute", record)
    try:
        response = client.request(method, path, headers=create_auth_header(who))
    finally:
        event.remove(bind, "before_cursor_execute", record)

    conn = db_session.connection()
    plans = [
        " | ".join(row[-1] for row in conn.exec_driver_sql("EXPLAIN QUERY PLAN " + statement, parameters))
        for statement, parameters in seen
    ]
    return response, plans


def _assert_served_by(plans, table, index):
    assert plans, (
        f"the request issued no SELECT against {table}, so there is no plan to "
        "read and this test would pass against anything"
    )
    scans = [plan for plan in plans if f"SCAN {table}" in plan]
    assert scans == [], f"{table} is read by a full scan: {scans}"
    assert any(index in plan for plan in plans), (
        f"no query against {table} uses {index}: {plans}"
    )


def _item_id(db_session, serial_number):
    return db_session.query(models.Equipment.id).filter_by(serial_number=serial_number).scalar()


@pytest.mark.parametrize(
    "who, path, table, index",
    [
        ("u_soldier_a", "/users/me/equipment", "equipment", "ix_equipment_holder_user_id"),
        # The holder arm of the scope predicate: a soldier holds no VIEW grant,
        # so possession is the only way this listing finds anything.
        ("u_soldier_a", "/equipment/accessible", "equipment", "ix_equipment_holder_user_id"),
        ("u_cmdr_a", "/tickets/", "maintenance_logs", "ix_maintenance_logs_equipment_id"),
        ("u_cmdr_a", "/verifications/equipment/{item}", "verifications",
         "ix_verifications_equipment_id"),
        ("u_cmdr_a", "/equipment/{item}/history", "equipment_status_history",
         "ix_equipment_status_history_equipment_id"),
    ],
    ids=["my_equipment", "scope_holder_arm", "tickets", "verifications", "history"],
)
def test_each_equipment_derived_read_goes_through_its_index(
    client, mock_matrix_db, db_session, who, path, table, index
):
    """The ticket's complaint, asked of the planner rather than of the metadata.

    Every one of these was a scan of a table that only grows. Each row names
    the index that replaces it, and the request that needs it.

    Asserted on EMPTY child tables and without statistics, deliberately. An
    equality on an indexed column is the plan SQLite picks knowing nothing
    about the data, so this cannot go quiet because a fixture happened to be
    small. The one index below that needs statistics to be chosen has its own
    test, and says so.
    """
    path = path.format(item=_item_id(db_session, "SA100"))

    response, plans = _plans(client, db_session, who, "GET", path, table)

    assert response.status_code == 200, response.text
    _assert_served_by(plans, table, index)


def test_the_fault_type_usage_count_goes_through_its_index(client, mock_matrix_db, db_session):
    """DATA-H7's pre-check counts the tickets holding a fault type.

    It runs on every delete of a fault type, against the ticket table, on a
    column nothing indexed -- so the guard that turned a 500 into a 409 was
    itself a scan of every ticket ever filed.
    """
    fault = models.FaultType(name="Cracked housing")
    db_session.add(fault)
    db_session.commit()

    response, plans = _plans(
        client, db_session, "u_master", "DELETE", f"/setup/fault_types/{fault.id}", "maintenance_logs"
    )

    assert response.status_code == 200, response.text
    _assert_served_by(plans, "maintenance_logs", "ix_maintenance_logs_fault_type_id")


DAY_INDEXES = (
    "ix_transaction_logs_group_id_timestamp",
    "ix_transaction_logs_from_group_id_timestamp",
)


def test_each_arm_of_the_daily_movement_report_ranges_over_its_composite_index(
    client, mock_matrix_db, db_session, group_graph
):
    """DATA-M9-2. The two indexes here that lead with a foreign key and go on
    to the timestamp, and why each is there.

    The report is one group's calendar day, the rows filed under the group
    through group_id or through from_group_id, as a UNION of two SELECTs. Each
    arm is an IN on its group column and a range on the timestamp, and each
    has the composite that serves exactly that, so the planner seeks the
    group's rows for the day and reads nothing else. Before DATA-M9-2 one
    index on the timestamp alone served a 24-hour window over every item the
    caller could see; it was dropped with the window.

    Measured on SQLite at 200,000 rows over two years, 38 companies, one
    company's past day (7 rows), the route's own statement: 0.07 ms with the
    two composites, 8.8 ms with the pair it had before (the timestamp alone
    and from_group_id alone), 126 ms with neither. On Postgres (pgserver,
    same seed, EXPLAIN ANALYZE, not asserted here): a bitmap index scan on
    each composite, then the day's ids fetched by primary key, 0.4 ms. That
    second half needs the route to JOIN the union rather than test
    `id IN (union)`: with the IN, Postgres read and joined all 200,000 rows
    first, 226 ms, while SQLite planned both forms alike, so this test cannot
    see the difference.

    NEEDS STATISTICS, unlike the tests above, and that is a property of the
    plan rather than a convenience of the test. Knowing nothing about the
    table, SQLite can prefer the equality on equipment_id that the join to
    the item offers, and the composites go unused. They are chosen once
    ANALYZE has told the planner how few rows a group's day is -- which is
    the situation of any database old enough for the question to matter.
    Forty rows over fifteen days, on one item, is enough to say so.

    maintenance_logs.opened_at was indexed on the same reasoning as the old
    timestamp index and then not, because a measurement refuted it: the
    ticket list has no LIMIT, so every visible ticket is fetched and sorted
    whatever index exists. See f4d81b2c6a93's docstring.
    """
    item = _item_id(db_session, "SA100")
    company_a, company_b = group_graph["188/53/A"].id, group_graph["188/53/B"].id
    tz = clock.zone(clock.DEFAULT_TIMEZONE)
    day = clock.today_in(tz) - timedelta(days=10)
    start, end = clock.day_bounds(tz, day)
    noon = start + (end - start) / 2

    # Every row is filed under A: half through group_id (they arrived), half
    # only through from_group_id (they left for B). So each arm has rows to
    # find in the day, and each arm's index is what finds them.
    logs = [
        models.TransactionLog(
            equipment_id=item, event_type="HANDOVER", timestamp=noon - timedelta(hours=9 * (i - 20)),
            group_id=company_a if i % 2 else company_b,
            from_group_id=None if i % 2 else company_a,
        )
        for i in range(40)
    ]
    db_session.add_all(logs)
    db_session.commit()
    in_day = {log.id for log in logs if start <= log.timestamp < end}
    db_session.execute(text("ANALYZE"))

    try:
        response, plans = _plans(
            client, db_session, "u_cmdr_a", "GET",
            f"/reports/daily_movement?date={day.isoformat()}", "transaction_logs",
        )
    finally:
        # The suite shares one in-memory database. Dropping the tables takes
        # their statistics rows with them; this takes the table that held them,
        # so no later test is planned against this one's leftovers.
        db_session.execute(text("DROP TABLE IF EXISTS sqlite_stat1"))
        db_session.commit()

    assert response.status_code == 200, response.text
    assert {row["id"] for row in response.json()["items"]} == in_day
    arms = {log.id for log in logs if log.id in in_day and log.from_group_id is not None}
    assert 0 < len(arms) < len(in_day), (
        "the seed no longer puts rows of both arms in the day, so one index "
        "would be asserted over no rows of its own"
    )
    for index in DAY_INDEXES:
        _assert_served_by(plans, "transaction_logs", index)


# --- the revision, against each state it will meet --------------------------

def test_the_revision_adds_exactly_the_indexes_it_names(tmp_path):
    """Nothing more, nothing less, each on its own column and none unique.

    A unique index on a foreign key is not a faster index, it is a rule: two
    items could no longer share a holder. The parity test would notice a model
    and a revision disagreeing about that; this notices them agreeing on the
    wrong thing.
    """
    expected = _expected_indexes()
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    _upgrade(engine, PARENT_REVISION)
    before = _indexes(engine)

    _upgrade(engine, INDEX_REVISION)
    after = _indexes(engine)
    engine.dispose()

    assert set(expected).isdisjoint(before), (
        "the parent revision already carries some of these, so the difference "
        "below would not be this revision's work"
    )
    assert set(after) - set(before) == set(expected)
    assert set(before) - set(after) == set(), "the revision removed an index"
    assert {name: after[name] for name in expected} == {
        name: (table, (column,), False) for name, (table, column) in expected.items()
    }


def test_a_database_missing_one_index_gets_that_one_and_no_other(tmp_path):
    """The skip is per index, not all-or-nothing.

    A database can hold some of these and not others -- an operator added the
    very index this revision builds, under the conventional name, to rescue a
    slow page, or a create_all database was built from a models.py snapshot
    partway through. (An index of a DIFFERENT shape under one of these names is
    the next test.) Two wrong
    versions of the skip both pass every other test: "skip the revision if any
    index is present" leaves the rest uncreated in silence, and "run it all
    unless every index is present" fails on the first duplicate.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'partial.db'}")
    _upgrade(engine, INDEX_REVISION)
    complete = _schema_snapshot(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP INDEX ix_verifications_created_by")

    with recorded(engine) as statements, engine.begin() as conn:
        _rerun_upgrade(conn, _script(), INDEX_REVISION)
    repaired = _schema_snapshot(engine)
    engine.dispose()

    created = [s for s in statements if "CREATE INDEX" in s.upper()]
    assert len(created) == 1 and "ix_verifications_created_by" in created[0], created
    assert repaired == complete


IMPOSTORS = {
    "wrong_column": (
        "CREATE INDEX ix_verifications_created_by ON verifications (equipment_id)",
        "ix_verifications_created_by",
        "equipment_id",
    ),
    "unique": (
        "CREATE UNIQUE INDEX ix_equipment_holder_user_id ON equipment (holder_user_id)",
        "ix_equipment_holder_user_id",
        "UNIQUE",
    ),
    "partial": (
        "CREATE INDEX ix_maintenance_logs_fault_type_id ON maintenance_logs (fault_type_id) WHERE fault_type_id = 1",
        "ix_maintenance_logs_fault_type_id",
        "partial",
    ),
    "another_table": (
        # The same column name on the wrong table, so that nothing but the
        # table can give it away.
        "CREATE INDEX ix_transaction_logs_equipment_id ON maintenance_logs (equipment_id)",
        "ix_transaction_logs_equipment_id",
        "on maintenance_logs",
    ),
}


@pytest.mark.parametrize("impostor", sorted(IMPOSTORS))
def test_an_index_of_another_shape_under_the_same_name_is_refused(tmp_path, impostor):
    """The skip is by name, so the name has to be checked rather than believed.

    One impostor per run, so each way of being the wrong index is refused on
    its own account rather than riding on another:

      - on the wrong column, or covering only some rows: the revision would be
        recorded as applied while the lookup it exists for still scans;
      - UNIQUE: that lookup is served, and the index is a rule nobody declared
        -- on holder_user_id it forbids a second item sharing a holder;
      - on another table: neither dialect scopes an index name to its table, so
        the name is simply taken, and without this check the upgrade fails
        partway with the driver's message and some of its indexes built.

    The schema must be untouched afterwards, including the indexes that were
    simply missing. The check runs before the first CREATE INDEX precisely so a
    refused upgrade leaves nothing half done.
    """
    statement, name, telltale = IMPOSTORS[impostor]
    engine = create_engine(f"sqlite:///{tmp_path / 'impostor.db'}")
    _upgrade(engine, PARENT_REVISION)
    with engine.begin() as conn:
        conn.exec_driver_sql(statement)
    before = _schema_snapshot(engine)

    with pytest.raises(RuntimeError) as excinfo:
        _upgrade(engine, INDEX_REVISION)
    message = str(excinfo.value)
    after = _schema_snapshot(engine)
    engine.dispose()

    assert name in message and telltale in message, message
    assert after == before, "the refusal changed the schema; it must change nothing"


def test_every_conflict_is_reported_and_a_correct_index_is_not(tmp_path):
    """All of them in one message, and only them.

    An operator who learns about conflicts one failed start at a time is the
    other failure the refusal avoids. And an index that is exactly what this
    revision would have built must not be named beside them: the refusal is for
    what is wrong, not for what is merely there.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'impostors.db'}")
    _upgrade(engine, PARENT_REVISION)
    with engine.begin() as conn:
        for statement, _name, _telltale in IMPOSTORS.values():
            conn.exec_driver_sql(statement)
        conn.exec_driver_sql(
            "CREATE INDEX ix_equipment_owner_user_id ON equipment (owner_user_id)"
        )

    with pytest.raises(RuntimeError) as excinfo:
        _upgrade(engine, INDEX_REVISION)
    message = str(excinfo.value)
    engine.dispose()

    unreported = [name for _statement, name, _telltale in IMPOSTORS.values() if name not in message]
    assert unreported == [], f"conflicts left out of the refusal: {unreported}\n{message}"
    assert "ix_equipment_owner_user_id" not in message, (
        f"an index of exactly the right shape was named as a conflict: {message}"
    )


def _parent_snapshot(tmp_path):
    """The schema of a database that was only ever upgraded to the parent."""
    reference = create_engine(f"sqlite:///{tmp_path / 'parent.db'}")
    _upgrade(reference, PARENT_REVISION)
    snapshot = _schema_snapshot(reference)
    reference.dispose()
    return snapshot


def test_the_downgrade_removes_what_the_upgrade_added_and_nothing_else(tmp_path):
    """A round trip lands on the parent's schema, and comes back to this one's.

    Compared against a database that was only ever upgraded to the parent, so
    "nothing else" includes the indexes that were already there: ix_equipment_id
    and ix_equipment_group_id belong to earlier revisions and a downgrade that
    dropped by pattern rather than by name would take them too.
    """
    at_parent = _parent_snapshot(tmp_path)

    engine = create_engine(f"sqlite:///{tmp_path / 'roundtrip.db'}")
    _upgrade(engine, INDEX_REVISION)
    at_revision = _schema_snapshot(engine)

    _downgrade(engine, PARENT_REVISION)
    downgraded = _schema_snapshot(engine)
    _upgrade(engine, INDEX_REVISION)
    upgraded_again = _schema_snapshot(engine)
    engine.dispose()

    assert at_revision != at_parent, "the revision changes nothing, so this proves nothing"
    assert downgraded == at_parent
    assert upgraded_again == at_revision


def test_the_downgrade_tolerates_an_index_that_is_already_gone(tmp_path):
    """The mirror of the partial upgrade above.

    A downgrade is run when something has already gone wrong. One that stops on
    the first index it cannot find leaves the database between two revisions,
    with the version table still naming the newer one.
    """
    at_parent = _parent_snapshot(tmp_path)

    engine = create_engine(f"sqlite:///{tmp_path / 'partial.db'}")
    _upgrade(engine, INDEX_REVISION)
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP INDEX ix_equipment_holder_user_id")

    _downgrade(engine, PARENT_REVISION)
    downgraded = _schema_snapshot(engine)
    engine.dispose()

    assert downgraded == at_parent


def test_a_pre_alembic_database_without_the_indexes_is_migrated_not_refused(
    tmp_path, monkeypatch
):
    """The database this ticket was most likely to lock out of its own fix.

    Real tables, real rows, no version table, and a schema from before this
    today's models: exactly what create_all built the day before this landed.
    describe_drift used to count each missing index, so run_migrations refused
    to baseline it -- and the revision that creates those indexes only runs
    after the baseline it was refused. It is now stamped and carried to head.

    The rows are the awkward ones an index has to be built over: holders that
    are NULL, a holder shared by two items, a log pointing at no item at all.
    None of that may stop the upgrade, and none of it may change.
    """
    path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{path}")
    _upgrade(engine, PARENT_REVISION)
    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO groups (id, name, kind) VALUES (1, '188', 'UNIT')")
        conn.exec_driver_sql("INSERT INTO catalog_items (id, name) VALUES (1, 'M4')")
        conn.exec_driver_sql("INSERT INTO users (id, personal_number) VALUES (1, 'u1')")
        conn.exec_driver_sql(
            "INSERT INTO equipment (id, catalog_item_id, group_id, holder_user_id) "
            "VALUES (1, 1, 1, NULL), (2, 1, 1, NULL), (3, 1, 1, 1), (4, 1, 1, 1)"
        )
        # An event_type, because head refuses a row without one (DATA-M8-1).
        conn.exec_driver_sql(
            "INSERT INTO transaction_logs (id, equipment_id, timestamp, event_type) "
            "VALUES (1, NULL, NULL, 'HANDOVER'), (2, 3, '2026-01-01 00:00:00.000000', 'HANDOVER')"
        )
        conn.exec_driver_sql("DROP TABLE alembic_version")
    engine.dispose()

    engine = create_engine(f"sqlite:///{path}")
    monkeypatch.setattr(migrations, "engine", engine)
    migrations.run_migrations()

    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        holders = conn.execute(text("SELECT id, holder_user_id FROM equipment ORDER BY id")).all()
        logs = conn.execute(text("SELECT id, equipment_id FROM transaction_logs ORDER BY id")).all()
    present = set(_indexes(engine))
    engine.dispose()

    assert version == _script().get_current_head()
    # Less the one whose column b7e3d91c4f2a (DATA-M8-1) drops on the way to
    # head (tests/test_movement_history.py holds that revision), and the one
    # 3c6a0f8e2d71 (DATA-M9-2) replaces with a composite
    # (tests/test_daily_movement_day.py).
    assert set(_expected_indexes()) - {
        "ix_transaction_logs_involved_location_id",
        "ix_transaction_logs_timestamp",
    } <= present
    assert "ix_transaction_logs_timestamp" not in present
    assert set(DAY_INDEXES) <= present
    assert holders == [(1, None), (2, None), (3, 1), (4, 1)]
    assert logs == [(1, None), (2, 3)]
