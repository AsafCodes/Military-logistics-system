"""
DATA-H13-2 -- the deletion rule on every foreign key.

tests/test_group_schema.py holds the structural promises: every foreign key the
models declare carries a rule, every one outside the group tables is named
fk_<table>_<column>, nothing outside the group tables is
softer than RESTRICT, and a migrated schema matches a create_all one. This file
holds the revision that gets an existing database there.

MOST OF THIS READS THE SCHEMA BACK rather than deleting a row to see what
happens, and the reason is the one thing worth knowing before adding a test. A
foreign key with no rule refuses the delete of a referenced row exactly as one
with RESTRICT does, so "delete the parent, expect an IntegrityError" passes
before this ticket and after it. The rule is visible only in what the database
says about its own constraints.

The one behavioural test here is aimed at what behaviour CAN show: that after
the rebuild each constraint is still enforced at all, and is not one of the
softer rules -- under CASCADE or SET NULL the same delete succeeds.
"""
import shutil

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError

from backend import migrations
from backend.database import _enforce_sqlite_foreign_keys
from tests.conftest import recorded
from tests.test_group_schema import (
    _downgrade,
    _rerun_upgrade,
    _schema_snapshot,
    _upgrade,
)

RULES_REVISION = "a8c2e5f19b47"
# Every index and constraint of today, and no deletion rule outside the group
# tables.
PARENT_REVISION = "f4d81b2c6a93"

# The migration tests below that downgrade, or compare a schema, stop at
# RULES_REVISION rather than at head, for the reason
# tests/test_foreign_key_indexes.py gives: a later revision's downgrade is that
# revision's business. The pre-Alembic test goes to head, as run_migrations()
# always does.


def _script():
    return ScriptDirectory.from_config(migrations.alembic_config())


def _expected_foreign_keys():
    """(table, column) -> referenced table, read off the revision.

    A second list here would be free to disagree with the first, and the
    revision is the one that ships.
    """
    module = _script().get_revision(RULES_REVISION).module
    return {(table, column): referred for table, column, referred in module.FOREIGN_KEYS}


EXPECTED = _expected_foreign_keys()
TABLES = sorted({table for table, _column in EXPECTED})


def _ruled(rule="RESTRICT"):
    """What _foreign_keys returns once every constraint is named and carries `rule`."""
    return {
        (table, column): (f"fk_{table}_{column}", referred, rule)
        for (table, column), referred in EXPECTED.items()
    }


def _foreign_keys(engine):
    """(table, column) -> (name, referenced table, ondelete), for the tables the revision touches.

    Refuses to answer if a column carries two foreign keys. A dict keyed by
    column would keep one and hide the other, and a replacement that ADDED the
    new constraint without removing the old is exactly the failure a
    drop-and-create can have.
    """
    insp = inspect(engine)
    found = [
        ((table, fk["constrained_columns"][0]), fk)
        for table in TABLES
        for fk in insp.get_foreign_keys(table)
    ]
    keys = [key for key, _fk in found]
    assert len(keys) == len(set(keys)), f"a column carries more than one foreign key: {sorted(keys)}"
    return {
        key: (fk["name"], fk["referred_table"], (fk.get("options") or {}).get("ondelete"))
        for key, fk in found
    }


def _seed(conn):
    """One row in every table the revision rebuilds, referencing through every
    foreign key it lists -- and one more that points nowhere."""
    conn.exec_driver_sql("INSERT INTO groups (id, name, kind) VALUES (1, '188', 'UNIT')")
    conn.exec_driver_sql("INSERT INTO catalog_items (id, name) VALUES (1, 'M4')")
    conn.exec_driver_sql("INSERT INTO locations (id, name) VALUES (1, 'Armory')")
    conn.exec_driver_sql("INSERT INTO users (id, personal_number) VALUES (1, 'u1')")
    conn.exec_driver_sql(
        "INSERT INTO equipment (id, serial_number, catalog_item_id, group_id, status, "
        "holder_user_id, owner_user_id, owner_location_id, actual_location_id) "
        "VALUES (1, 'SN-1', 1, 1, 'Functional', 1, 1, 1, 1)"
    )
    # The dangling reference: holder 999 does not exist. Reachable because the
    # migration engine runs without enforcement, as it must.
    conn.exec_driver_sql(
        "INSERT INTO equipment (id, serial_number, catalog_item_id, group_id, holder_user_id) "
        "VALUES (2, 'SN-2', 1, 1, 999)"
    )
    conn.exec_driver_sql("INSERT INTO fault_types (id, name, requested_by_id) VALUES (1, 'Jam', 1)")
    conn.exec_driver_sql(
        "INSERT INTO maintenance_logs (id, equipment_id, fault_type_id, status, technician_id) "
        "VALUES (1, 1, 1, 'Open', 1)"
    )
    conn.exec_driver_sql(
        "INSERT INTO transaction_logs (id, equipment_id, involved_user_id, involved_location_id) "
        "VALUES (1, 1, 1, 1)"
    )
    conn.exec_driver_sql(
        "INSERT INTO verifications (id, equipment_id, verification_type, reported_status, created_by) "
        "VALUES (1, 1, 'routine', 'Functional', 1)"
    )
    conn.exec_driver_sql(
        "INSERT INTO equipment_status_history "
        "(id, equipment_id, old_status, new_status, change_reason, verification_id, created_by) "
        "VALUES (1, 1, 'Functional', 'Missing', 'verification', 1, 1)"
    )


def _rows(engine):
    """Every row of every rebuilt table, as stored."""
    with engine.connect() as conn:
        return {
            table: conn.execute(text(f"SELECT * FROM {table} ORDER BY id")).all()
            for table in TABLES
        }


def _replace_foreign_keys(engine, table, columns, *, name, ondelete):
    """Hand-build a state the revision has to cope with.

    Drops the foreign key on each of `columns` and, unless `name` is None,
    re-creates it named `name(table, column)` with `ondelete`.

    The constraint being dropped is always addressed as fk_<table>_<column>.
    That is its real name on a database already at RULES_REVISION, and on one
    at the parent revision -- where it is anonymous -- it is the name the
    convention below gives it for the duration of the rebuild.
    """
    convention = {"fk": "fk_%(table_name)s_%(column_0_name)s"}
    with (
        engine.begin() as conn,
        Operations.context(MigrationContext.configure(conn)) as op,
        op.batch_alter_table(table, naming_convention=convention) as batch_op,
    ):
        for column in columns:
            batch_op.drop_constraint(f"fk_{table}_{column}", type_="foreignkey")
            if name is not None:
                batch_op.create_foreign_key(
                    name(table, column), EXPECTED[(table, column)], [column], ["id"], ondelete=ondelete
                )


def _columns_of(table):
    return [column for fk_table, column in EXPECTED if fk_table == table]


def _rebuilt_tables(statements):
    """Which tables a batch of recorded SQL rebuilt. Batch mode copies into _alembic_tmp_<table>."""
    return sorted({
        table for table in TABLES for s in statements
        if f"CREATE TABLE _alembic_tmp_{table} " in s
    })


# --- what the revision does to a database that has never seen it ------------

def test_the_revision_names_and_rules_every_foreign_key_it_lists(tmp_path):
    """Before: every one of them without a rule. After: every one named
    fk_<table>_<column> and RESTRICT, still pointing where it did.

    The 'before' half is asserted rather than assumed. If the parent revision
    ever came to carry these rules, the 'after' half would be describing
    someone else's work and this test would go on passing.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'rules.db'}")
    _upgrade(engine, PARENT_REVISION)
    before = _foreign_keys(engine)

    _upgrade(engine, RULES_REVISION)
    after = _foreign_keys(engine)
    engine.dispose()

    assert {key: referred for key, (_name, referred, _rule) in before.items()} == EXPECTED, (
        "the revision lists a foreign key that does not exist, or points one somewhere new"
    )
    assert {key: rule for key, (_name, _referred, rule) in before.items()} == dict.fromkeys(EXPECTED)
    assert after == _ruled()


def test_the_rebuild_keeps_every_row_as_it_was(tmp_path):
    """On SQLite, replacing a rule copies the whole table. Nothing may change.

    Six tables are rebuilt, each by create-copy-drop-rename, and a column the
    copy forgot would arrive NULL without any error. So every column of every
    row is compared, not a count.

    One of the rows points at a user who does not exist. The rebuild carries
    it across untouched: this revision neither finds nor repairs a dangling
    reference, says so in its docstring, and must not start doing either by
    accident -- a migration that silently deleted or nulled such a row would
    be making a data decision nobody asked it for.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'rows.db'}")
    _upgrade(engine, PARENT_REVISION)
    with engine.begin() as conn:
        _seed(conn)
    before = _rows(engine)

    with recorded(engine) as statements:
        _upgrade(engine, RULES_REVISION)
    after = _rows(engine)
    engine.dispose()

    assert _rebuilt_tables(statements) == TABLES, "a table was not rebuilt, so its copy is untested"
    assert all(before.values()), "a rebuilt table was left unseeded, so its copy is untested"
    assert after == before
    assert (2, 999) in [(row.id, row.holder_user_id) for row in after["equipment"]]


def test_a_pre_alembic_database_is_carried_to_the_rules_with_its_rows(tmp_path, monkeypatch):
    """The baseline path, end to end, with data in the way.

    Real tables, real rows, no version table: what create_all built the day
    before this landed. describe_drift must not read foreign keys that differ
    in name and rule as a schema that is missing something, and the stamp
    must land before this revision rather than past it.
    """
    path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{path}")
    _upgrade(engine, PARENT_REVISION)
    with engine.begin() as conn:
        _seed(conn)
        conn.exec_driver_sql("DROP TABLE alembic_version")
    before = _rows(engine)
    engine.dispose()

    engine = create_engine(f"sqlite:///{path}")
    monkeypatch.setattr(migrations, "engine", engine)
    migrations.run_migrations()

    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    keys = _foreign_keys(engine)
    after = _rows(engine)
    engine.dispose()

    assert version == _script().get_current_head()
    assert keys == _ruled()
    assert after == before


# --- the states a real database can already be in ---------------------------

@pytest.mark.parametrize("rule", [None, "RESTRICT"], ids=["unruled", "already_restrict"])
def test_a_constraint_named_the_way_postgres_names_it_is_replaced_not_duplicated(tmp_path, rule):
    """The Postgres path's most fragile step, rehearsed on SQLite.

    Twice: once as Postgres actually has them, with no rule, and once already
    RESTRICT under the server's name. The second is not a state this chain
    produces. It is there because the skip needs BOTH halves of its condition,
    and a constraint that has the rule and not the name is the only thing
    that separates "named and ruled" from "ruled" -- the models name every one
    of these, so leaving the server's name in place is a schema the parity
    test would call wrong.


    On Postgres none of these constraints is anonymous: the server named each
    one <table>_<column>_fkey when the initial revision created it. The
    revision must drop it under THAT name, which it does not hard-code anywhere
    repository and has to be read from the database. Drop it under the name
    the revision is about to use instead and Postgres raises; forget the drop
    and the column ends up with two constraints.

    SQLite cannot be Postgres, but it can be handed the same names. This is a
    rehearsal and not the performance: the statements Postgres runs are ALTER
    TABLE rather than a rebuild, and only CI's Postgres job runs those.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'pgnames.db'}")
    _upgrade(engine, PARENT_REVISION)
    for table in TABLES:
        columns = [c for c in _columns_of(table) if (table, c) != ("equipment", "group_id")]
        _replace_foreign_keys(
            engine, table, columns, name=lambda t, c: f"{t}_{c}_fkey", ondelete=rule
        )
    before = _foreign_keys(engine)

    _upgrade(engine, RULES_REVISION)
    after = _foreign_keys(engine)
    engine.dispose()

    assert before[("verifications", "created_by")][0] == "verifications_created_by_fkey", (
        "the setup did not produce Postgres-style names, so this rehearses nothing"
    )
    assert after == _ruled()


def test_a_rule_somebody_chose_later_is_not_changed_back(tmp_path):
    """Named as this revision names it, and CASCADE: leave it alone.

    This revision re-runs against whatever create_all builds from the models
    of the day, on every database that takes the baseline path. If one of
    these foreign keys is ever deliberately changed to another rule, a skip
    that insisted on RESTRICT would quietly change it back -- and emit DDL on
    a re-run, which is the thing an allowlisted revision promises not to do.

    So the skip asks whether a rule was declared, not which.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'later.db'}")
    _upgrade(engine, RULES_REVISION)
    _replace_foreign_keys(
        engine, "maintenance_logs", ["equipment_id"],
        name=lambda t, c: f"fk_{t}_{c}", ondelete="CASCADE",
    )

    with recorded(engine) as statements, engine.begin() as conn:
        _rerun_upgrade(conn, _script(), RULES_REVISION)
    after = _foreign_keys(engine)
    engine.dispose()

    assert _rebuilt_tables(statements) == []
    assert after[("maintenance_logs", "equipment_id")] == (
        "fk_maintenance_logs_equipment_id", "equipment", "CASCADE"
    )


def test_a_later_rule_survives_a_rebuild_its_neighbour_needed(tmp_path):
    """A rule chosen later and a rule gone missing, in one table.

    maintenance_logs has three foreign keys. One was deliberately made CASCADE
    after this revision; another has lost its rule. The table has to be
    rebuilt for the second, and the rebuild must not take the opportunity to
    put the first back to RESTRICT -- which a skip decided per TABLE rather
    than per foreign key would do.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'mixed.db'}")
    _upgrade(engine, RULES_REVISION)
    _replace_foreign_keys(
        engine, "maintenance_logs", ["equipment_id"],
        name=lambda t, c: f"fk_{t}_{c}", ondelete="CASCADE",
    )
    _replace_foreign_keys(
        engine, "maintenance_logs", ["technician_id"],
        name=lambda t, c: f"fk_{t}_{c}", ondelete=None,
    )
    arranged = _foreign_keys(engine)
    assert arranged[("maintenance_logs", "equipment_id")][2] == "CASCADE", "setup failed"
    assert arranged[("maintenance_logs", "technician_id")][2] is None, "setup failed"

    with recorded(engine) as statements, engine.begin() as conn:
        _rerun_upgrade(conn, _script(), RULES_REVISION)
    after = _foreign_keys(engine)
    engine.dispose()

    assert _rebuilt_tables(statements) == ["maintenance_logs"], (
        "the table was not rebuilt, so the CASCADE surviving proves nothing"
    )
    expected = _ruled()
    expected[("maintenance_logs", "equipment_id")] = (
        "fk_maintenance_logs_equipment_id", "equipment", "CASCADE"
    )
    assert after == expected


def test_only_the_table_that_needs_it_is_rebuilt(tmp_path):
    """A database with five tables done and one not gets one rebuild.

    A rebuild copies every row of the table, so doing six because one needed
    it is not harmless on a database of any size -- and "skip the revision if
    anything is already done" would leave the sixth unruled in silence.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'partial.db'}")
    _upgrade(engine, RULES_REVISION)
    complete = _schema_snapshot(engine)
    _replace_foreign_keys(
        engine, "verifications", _columns_of("verifications"),
        name=lambda t, c: f"fk_{t}_{c}", ondelete=None,
    )
    assert _foreign_keys(engine)[("verifications", "created_by")][2] is None, "setup failed"

    with recorded(engine) as statements, engine.begin() as conn:
        _rerun_upgrade(conn, _script(), RULES_REVISION)
    repaired = _schema_snapshot(engine)
    engine.dispose()

    assert _rebuilt_tables(statements) == ["verifications"]
    assert repaired == complete


def test_a_foreign_key_that_is_missing_altogether_is_created(tmp_path):
    """No constraint on the column at all: nothing to drop, and one to build.

    A table rebuilt by hand, or restored from a dump that left its constraints
    behind. The column still holds references; this is the revision that says
    what they are.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'missing.db'}")
    _upgrade(engine, PARENT_REVISION)
    _replace_foreign_keys(engine, "fault_types", ["requested_by_id"], name=None, ondelete=None)
    assert ("fault_types", "requested_by_id") not in _foreign_keys(engine), "setup failed"

    _upgrade(engine, RULES_REVISION)
    after = _foreign_keys(engine)
    engine.dispose()

    assert after == _ruled()


# --- the downgrade ----------------------------------------------------------

def test_the_downgrade_removes_the_rule_and_keeps_the_name(tmp_path):
    """Reversible for the thing that matters, and honest about the rest.

    Batch mode cannot create a constraint without a name, so the anonymous
    ones cannot be made anonymous again on SQLite. The downgrade therefore
    takes away what this revision decided -- the rule -- and leaves what it
    merely had to do on the way. Everything else about the schema is the
    parent's.

    Then up again, because a downgrade that leaves the database somewhere the
    upgrade cannot start from is discovered at the worst possible moment: the
    foreign keys are now named and unruled, a state no other path produces.
    """
    reference = create_engine(f"sqlite:///{tmp_path / 'parent.db'}")
    _upgrade(reference, PARENT_REVISION)
    at_parent = _schema_snapshot(reference)
    reference.dispose()

    engine = create_engine(f"sqlite:///{tmp_path / 'roundtrip.db'}")
    _upgrade(engine, PARENT_REVISION)
    with engine.begin() as conn:
        _seed(conn)
    rows = _rows(engine)
    _upgrade(engine, RULES_REVISION)
    at_revision = _schema_snapshot(engine)

    _downgrade(engine, PARENT_REVISION)
    downgraded_keys = _foreign_keys(engine)
    downgraded = _schema_snapshot(engine)
    downgraded_rows = _rows(engine)

    _upgrade(engine, RULES_REVISION)
    upgraded_again = _schema_snapshot(engine)
    engine.dispose()

    assert downgraded_keys == _ruled(rule=None)
    for table in at_parent:
        without_keys = {k: v for k, v in downgraded[table].items() if k != "foreign_keys"}
        assert without_keys == {k: v for k, v in at_parent[table].items() if k != "foreign_keys"}, table
    assert downgraded_rows == rows
    assert upgraded_again == at_revision


def test_the_downgrade_leaves_alone_a_table_that_has_no_rule_to_remove(tmp_path):
    """The mirror of the partial upgrade: one table already unruled.

    A downgrade is run when something has already gone wrong, so it meets
    half-finished states more often than an upgrade does. It must finish, and
    it must not copy a table it has nothing to change in.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'partial-down.db'}")
    _upgrade(engine, RULES_REVISION)
    _replace_foreign_keys(
        engine, "verifications", _columns_of("verifications"),
        name=lambda t, c: f"fk_{t}_{c}", ondelete=None,
    )

    with recorded(engine) as statements:
        _downgrade(engine, PARENT_REVISION)
    after = _foreign_keys(engine)
    engine.dispose()

    assert _rebuilt_tables(statements) == [t for t in TABLES if t != "verifications"]
    assert after == _ruled(rule=None)


# --- what behaviour can show ------------------------------------------------

@pytest.fixture(scope="module")
def migrated_and_seeded(tmp_path_factory):
    """One migrated, seeded database file, copied per test rather than rebuilt."""
    path = tmp_path_factory.mktemp("rules") / "template.db"
    engine = create_engine(f"sqlite:///{path}")
    _upgrade(engine, RULES_REVISION)
    with engine.begin() as conn:
        _seed(conn)
    engine.dispose()
    return path


# A parent row nothing else references, per referenced table. id 50 throughout.
SPARE_PARENT = {
    "users": "INSERT INTO users (id, personal_number) VALUES (50, 'spare')",
    "locations": "INSERT INTO locations (id, name) VALUES (50, 'Spare')",
    "catalog_items": "INSERT INTO catalog_items (id, name) VALUES (50, 'Spare')",
    "groups": "INSERT INTO groups (id, name, kind) VALUES (50, 'spare', 'UNIT')",
    "fault_types": "INSERT INTO fault_types (id, name) VALUES (50, 'Spare')",
    "equipment": "INSERT INTO equipment (id, serial_number, catalog_item_id, group_id) VALUES (50, 'SPARE', 1, 1)",
    "verifications": (
        "INSERT INTO verifications (id, equipment_id, verification_type, reported_status, created_by) "
        "VALUES (50, 1, 'routine', 'Functional', 1)"
    ),
}


@pytest.mark.parametrize("table, column", sorted(EXPECTED), ids=lambda v: v)
def test_each_foreign_key_alone_refuses_the_delete_of_its_parent(
    tmp_path, migrated_and_seeded, table, column
):
    """One foreign key at a time, on a migrated database, with enforcement on.

    A spare parent row is created that nothing references, and exactly one
    column of one row is pointed at it. Deleting the spare must then fail --
    and must succeed again the moment that one column is pointed back, which
    is what shows the refusal came from this constraint and no other.

    WHAT THIS CAN AND CANNOT TELL. It cannot tell RESTRICT from no rule at
    all; both refuse. It does tell that the rebuild left each constraint
    standing and enforced, and it does tell RESTRICT from the softer rules:
    under CASCADE or SET NULL the first delete below simply succeeds.
    """
    referred = EXPECTED[(table, column)]
    path = tmp_path / "copy.db"
    shutil.copy(migrated_and_seeded, path)
    engine = create_engine(f"sqlite:///{path}")
    event.listen(engine, "connect", _enforce_sqlite_foreign_keys)

    with engine.begin() as conn:
        conn.exec_driver_sql(SPARE_PARENT[referred])
        conn.exec_driver_sql(f"UPDATE {table} SET {column} = 50 WHERE id = 1")

    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.exec_driver_sql(f"DELETE FROM {referred} WHERE id = 50")

    with engine.begin() as conn:
        survivor = conn.exec_driver_sql(f"SELECT {column} FROM {table} WHERE id = 1").scalar()
        conn.exec_driver_sql(f"UPDATE {table} SET {column} = 1 WHERE id = 1")
        conn.exec_driver_sql(f"DELETE FROM {referred} WHERE id = 50")
        remaining = conn.exec_driver_sql(f"SELECT count(*) FROM {referred} WHERE id = 50").scalar()
    engine.dispose()

    assert survivor == 50, "the refused delete still changed the referencing row"
    assert remaining == 0, "the spare parent could not be deleted even once nothing referenced it"
