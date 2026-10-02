"""
Schema coverage for H1-1 (TODO-SEC-H1.md) -- the group algebra tables.

H1-1 deliberately adds no behaviour: the closure engine is H1-2 and the query
surface is H1-3. What it does add is a set of promises encoded in DDL --
polymorphic identities, uniqueness, NOT NULL, composite keys, an ondelete rule
on every foreign key and index coverage for every foreign key column. These tests
check that those promises are real rather than merely declared, and pin the
one place where SQLite silently does not keep them.

Two tests here guard things that are invisible in ordinary use and expensive to
discover later: that the Alembic migration and Base.metadata.create_all() build
the same schema (the suite uses the second, CI and production use the first),
and that the batch-mode rebuild of `equipment` preserves its rows.
"""
import ast
import sqlite3
from pathlib import Path

import pytest
from alembic.script import ScriptDirectory
from sqlalchemy import UniqueConstraint, create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from backend import authz, database, migrations, models
from backend.database import Base
from backend.enums import Capability, GroupKind
from tests.conftest import recorded

BASELINE_REVISION = "4acc9d5f6339"
# The revision that added the group tables, before H1-11 dropped the legacy
# path columns. Several tests below need a database in exactly that state: it
# is what a create_all database looked like before H1-11, and what
# migrations.baseline_revision() stamps such a database as.
GROUPS_REVISION = migrations.REVISION_BEFORE_LEGACY_DROP
# H1-11's own revision -- the boundary this test needs, distinct from "head"
# now that H1-12 lands more migrations after it and would otherwise drop
# battalion/company out from under an assertion about H1-11 specifically.
LEGACY_DROP_REVISION = "c93f2a615d84"
# The last revision before DATA-H12's check constraints -- DATA-H5-2's
# backfill, and the same revision migrations.BASELINE_STAMP sits one behind.
# A database here has today's tables and none of the vocabulary constraints,
# which is the only state in which a row holding a bad status can be planted.
CONSTRAINTS_REVISION_PARENT = "6f821fc450b8"

NEW_TABLES = ("groups", "group_edges", "group_closure", "group_memberships", "grants")


def _upgrade(engine, revision):
    """Migrate `engine` to `revision`, the same way the application does.

    Reuses backend.migrations.alembic_config() so the ini location and the
    logger guard are not re-derived here -- a second copy would let the suite
    drift from what production runs, which is the very divergence
    test_migration_and_create_all_build_the_same_schema exists to catch.

    begin(), not connect(): Alembic runs inside the caller's transaction when
    handed a connection, so the alembic_version write needs a commit at exit
    (see run_migrations, which wraps the same call in engine.begin()).
    """
    cfg = migrations.alembic_config()
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, revision)


def _downgrade(engine, revision):
    """The mirror of _upgrade. command.upgrade() will not walk backwards.

    It does not fail either -- asked to reach a revision behind the current
    one it simply finds no path forward and does nothing, which reads as a
    passing downgrade until an assertion notices the schema never changed.
    """
    cfg = migrations.alembic_config()
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, revision)


# --- registration and polymorphism ---------------------------------------

def test_group_tables_are_registered_and_created(db_session):
    """models.py imports authz purely for the side effect of registering these."""
    present = set(inspect(db_session.get_bind()).get_table_names())
    assert set(NEW_TABLES) <= present
    assert "group_id" in {c["name"] for c in inspect(db_session.get_bind()).get_columns("equipment")}


def test_unit_and_task_force_round_trip_as_groups(db_session):
    """Single-table inheritance: both kinds live in `groups` and come back typed."""
    db_session.add_all([authz.Unit(name="Bn53"), authz.TaskForce(name="TF Sinai")])
    db_session.commit()

    by_name = {g.name: g for g in db_session.query(authz.Group).all()}
    assert isinstance(by_name["Bn53"], authz.Unit)
    assert isinstance(by_name["TF Sinai"], authz.TaskForce)
    assert by_name["Bn53"].kind == GroupKind.UNIT.value
    assert by_name["TF Sinai"].kind == GroupKind.TASK_FORCE.value

    # Both subclasses map to the one table -- no per-kind table was created.
    assert authz.Unit.__table__.name == authz.TaskForce.__table__.name == "groups"
    # Querying a subclass filters by polymorphic identity.
    assert [u.name for u in db_session.query(authz.Unit).all()] == ["Bn53"]


def test_the_polymorphic_base_cannot_be_persisted(db_session):
    """Only Unit and TaskForce are storable, so every stored kind is a real one.

    The base carries no polymorphic_identity, so a bare Group() leaves `kind`
    NULL and the NOT NULL constraint rejects it. Giving the base an identity
    would let kind='GROUP' persist -- a value outside GroupKind, which would
    make GroupKind(row.kind) unsafe for H1-2 and later.
    """
    db_session.add(authz.Group(name="bare"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # Every value the mapper can actually write is a GroupKind member.
    db_session.add_all([authz.Unit(name="u"), authz.TaskForce(name="t")])
    db_session.commit()
    assert {GroupKind(g.kind) for g in db_session.query(authz.Group).all()} == {
        GroupKind.UNIT,
        GroupKind.TASK_FORCE,
    }


# --- constraints that SQLite does enforce --------------------------------

def test_group_name_is_unique(db_session):
    db_session.add(authz.Unit(name="Bn53"))
    db_session.commit()
    db_session.add(authz.Unit(name="Bn53"))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_grant_triple_is_unique(db_session):
    """One row per (user, group, capability) -- re-granting must not duplicate."""
    user = models.User(personal_number="u1", full_name="One")
    group = authz.Unit(name="Bn53")
    db_session.add_all([user, group])
    db_session.commit()

    db_session.add(authz.Grant(user_id=user.id, group_id=group.id, capability=Capability.VIEW.value))
    db_session.commit()
    db_session.add(authz.Grant(user_id=user.id, group_id=group.id, capability=Capability.VIEW.value))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_closure_depth_is_not_null(db_session):
    group = authz.Unit(name="Bn53")
    db_session.add(group)
    db_session.commit()

    db_session.add(authz.GroupClosure(ancestor_id=group.id, descendant_id=group.id, depth=None))
    with pytest.raises(IntegrityError):
        db_session.commit()


@pytest.mark.parametrize(
    "table, columns, values",
    [
        ("group_edges", "parent_id, child_id", "1, 2"),
        ("group_closure", "ancestor_id, descendant_id, depth", "1, 2, 1"),
        ("group_memberships", "user_id, group_id", "1, 2"),
    ],
)
def test_composite_primary_keys_reject_duplicate_pairs(db_session, table, columns, values):
    """A DAG diamond reaches the same descendant twice; the pair must stay unique."""
    db_session.execute(text("INSERT INTO groups (id, name, kind) VALUES (1, 'a', 'UNIT')"))
    db_session.execute(text("INSERT INTO groups (id, name, kind) VALUES (2, 'b', 'UNIT')"))
    db_session.execute(text("INSERT INTO users (id, personal_number) VALUES (1, 'u1')"))

    stmt = text(f"INSERT INTO {table} ({columns}) VALUES ({values})")

    db_session.execute(stmt)
    with pytest.raises(IntegrityError):
        db_session.execute(stmt)


# --- H1-1's stated deliverable, asserted against the metadata -------------

def test_every_new_foreign_key_declares_an_ondelete_rule():
    """DATA-H13: no pre-existing FK declares one. The new tables must."""
    missing = [
        f"{table}.{next(iter(fk.columns)).name}"
        for table in NEW_TABLES
        for fk in Base.metadata.tables[table].foreign_key_constraints
        if fk.ondelete is None
    ]
    assert missing == [], f"foreign keys without an ondelete rule: {missing}"


def test_equipment_group_id_deliberately_has_no_ondelete_rule():
    """Deleting a group that still holds equipment must fail, not cascade."""
    fk = next(
        fk for fk in Base.metadata.tables["equipment"].foreign_key_constraints
        if fk.referred_table.name == "groups"
    )
    assert fk.ondelete is None


def test_every_foreign_key_column_is_covered_by_an_index():
    """Otherwise every scoped join degrades to a scan as the tables grow.

    "Covered" means usable as a leading column, not necessarily owning a
    dedicated index: a column that leads the composite primary key or a
    composite unique constraint is already served by that index, and adding a
    second single-column index on it would be a redundant prefix.

    Every table the models declare, as of DATA-H13-1. This walked NEW_TABLES
    alone until then, because the legacy tables indexed none of their
    seventeen foreign keys and the rule could only be asserted where it held.
    There is no exception list, on purpose: a foreign key added tomorrow
    without an index fails here rather than joining a list of known gaps.
    """
    uncovered = []
    for table_name, table in sorted(Base.metadata.tables.items()):
        leading = {next(iter(table.primary_key.columns)).name}
        leading |= {
            next(iter(c.columns)).name
            for c in table.constraints
            if isinstance(c, UniqueConstraint) and len(c.columns) > 0
        }
        # The LEADING column of each index, like the two sets above. This used
        # to take every column of every index, which would have counted a
        # foreign key sitting second in a composite as covered when no lookup
        # on it alone can use that index.
        indexed = {next(iter(idx.columns)).name for idx in table.indexes}
        indexed |= {c.name for c in table.columns if c.index}

        for fk in table.foreign_key_constraints:
            column = next(iter(fk.columns)).name
            if column not in leading and column not in indexed:
                uncovered.append(f"{table_name}.{column}")
    assert uncovered == [], f"foreign key columns no index can serve: {uncovered}"


def test_no_redundant_single_column_index_on_a_leading_key_column():
    """A dedicated index duplicating a composite key's prefix is write amplification.

    `grants` is the table H1-2 and H1-3 write most, so it is the one that would
    actually pay for the extra B-tree maintenance.
    """
    redundant = []
    for table_name in NEW_TABLES:
        table = Base.metadata.tables[table_name]
        composites = [tuple(c.name for c in table.primary_key.columns)]
        composites += [
            tuple(c.name for c in con.columns)
            for con in table.constraints
            if isinstance(con, UniqueConstraint)
        ]
        single = {next(iter(idx.columns)).name for idx in table.indexes if len(idx.columns) == 1}
        single |= {c.name for c in table.columns if c.index}
        for cols in composites:
            if len(cols) > 1 and cols[0] in single:
                redundant.append(f"{table_name}.{cols[0]} duplicates the prefix of {cols}")
        # A primary key already builds its own unique index.
        for column in table.primary_key.columns:
            if len(composites[0]) == 1 and column.index:
                redundant.append(f"{table_name}.{column.name} duplicates the primary key index")
    assert redundant == [], f"redundant indexes: {redundant}"


def test_the_ondelete_rules_depend_entirely_on_the_sqlite_pragma(tmp_path):
    """Why backend.database sets PRAGMA foreign_keys on every SQLite connection.

    Every ondelete rule above is unconditionally real on Postgres. SQLite
    parses the REFERENCES clause and then ignores it outright unless this
    pragma is set, per connection -- so the cascade below is the difference
    between a deleted group taking its edges with it and leaving debris that
    the next reused rowid adopts.

    Demonstrated on a raw sqlite3 connection rather than through the engine,
    because the point is what the database does on its own.
    """
    path = tmp_path / "fk.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(bind=engine)
    engine.dispose()

    def delete_parent(pragma_on):
        conn = sqlite3.connect(path)
        if pragma_on:
            conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("INSERT INTO groups (id, name, kind) VALUES (1, 'p', 'UNIT')")
        conn.execute("INSERT INTO groups (id, name, kind) VALUES (2, 'c', 'UNIT')")
        conn.execute("INSERT INTO group_edges (parent_id, child_id) VALUES (1, 2)")
        conn.execute("DELETE FROM groups WHERE id = 1")
        remaining = len(list(conn.execute("SELECT 1 FROM group_edges WHERE parent_id = 1")))
        conn.rollback()
        conn.close()
        return remaining

    assert delete_parent(pragma_on=False) == 1, "cascade unexpectedly fired without the pragma"
    assert delete_parent(pragma_on=True) == 0, "cascade did not fire with the pragma on"


def test_the_application_enforces_foreign_keys_and_migrations_do_not(tmp_path, monkeypatch):
    """The two engines must disagree, deliberately and in this direction.

    The application and the suite run under enforcement so declared cascades
    fire. Migrations must not: Alembic's batch_alter_table rebuilds a table by
    dropping and renaming it, and DROP TABLE equipment fails under enforcement
    because four tables reference it. The pragma cannot be toggled off for the
    duration either -- SQLite ignores it inside a transaction and says nothing
    -- so the separation has to be two engines. See create_database_engine.

    Asserted WITHOUT connecting to either singleton. This test used to open
    database.engine and migrations.engine and read the pragma straight off
    them, which worked only because tests/conftest.py pinned DATABASE_URL at a
    SQLite file it chose. Those engines now address whatever the environment
    names -- in CI a Postgres service, where PRAGMA foreign_keys does not exist
    and the old assertion would have failed for a dialect reason. DATA-H11's
    pin does not restore the old guarantee: it yields to an exported variable,
    which is exactly what CI sets, so it cannot be relied on to name SQLite.

    Neither assertion below is sufficient alone. event.contains reads the
    singletons' wiring without opening anything, and is what holds
    migrations.py's enforce_foreign_keys=False -- but a listener that is
    attached and does nothing would satisfy it just as well. So the pragma is
    also read for real, from engines built over a file this test owns.
    """
    assert event.contains(database.engine, "connect", database._enforce_sqlite_foreign_keys)
    assert not event.contains(migrations.engine, "connect", database._enforce_sqlite_foreign_keys)

    # create_database_engine reads the module global rather than accepting a
    # URL, so redirecting the global is the only way to aim it at a file this
    # test owns.
    monkeypatch.setattr(database, "DATABASE_URL", f"sqlite:///{(tmp_path / 'fk.db').as_posix()}")

    for enforce_foreign_keys, expected in ((True, 1), (False, 0)):
        engine = database.create_database_engine(enforce_foreign_keys=enforce_foreign_keys)
        try:
            with engine.connect() as conn:
                assert conn.execute(text("PRAGMA foreign_keys")).scalar() == expected
        finally:
            engine.dispose()


def test_the_test_suite_runs_under_the_applications_integrity_rules(db_session):
    """A suite with looser rules than production is how orphan rows survive review."""
    assert db_session.execute(text("PRAGMA foreign_keys")).scalar() == 1


# --- the migration itself -------------------------------------------------

def _legacy_row(conn, unit_hierarchy="188/53/A"):
    """One equipment row in the pre-H1-11 shape: a path string and no group."""
    conn.exec_driver_sql("INSERT INTO catalog_items (id, name) VALUES (1, 'M4')")
    conn.exec_driver_sql("INSERT INTO users (id, personal_number) VALUES (7, 'u7')")
    conn.exec_driver_sql(
        "INSERT INTO equipment (id, serial_number, catalog_item_id, status, unit_hierarchy,"
        " holder_user_id, custom_location) VALUES (1, 'SN-1', 1, 'Functional', ?, 7, 'Bay 1')",
        (unit_hierarchy,),
    )
    conn.exec_driver_sql("INSERT INTO transaction_logs (id, equipment_id, event_type) VALUES (1, 1, 'HANDOVER')")


def test_migration_preserves_equipment_rows_and_places_them(tmp_path):
    """The migration rebuilds `equipment` in batch mode on SQLite -- twice now.

    A copy-and-move that dropped or reordered data would be silent and
    unrecoverable, so upgrade across it with rows present.

    H1-11 added a second thing to check on the way through. The row below
    predates group_id and carries only the path string, which is what the
    backfill exists for: the path strings ARE the group names, and this is the
    last revision at which that mapping can still be read. If the backfill did
    nothing, the NOT NULL landing in the same revision would refuse the row and
    this test would fail at the upgrade rather than at the assertion.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'data.db'}")
    _upgrade(engine, GROUPS_REVISION)
    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO groups (id, name, kind) VALUES (4, '188/53/A', 'unit')")
        _legacy_row(conn)

    _upgrade(engine, LEGACY_DROP_REVISION)

    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT serial_number, catalog_item_id, status, holder_user_id,"
            " custom_location, group_id FROM equipment WHERE id = 1"
        )).one()
        assert row == ("SN-1", 1, "Functional", 7, "Bay 1", 4)
        # The rebuild must not strand rows in the tables that reference equipment.
        assert conn.execute(text("SELECT equipment_id FROM transaction_logs")).scalar() == 1
        assert conn.execute(text("SELECT count(*) FROM sqlite_master WHERE name LIKE '%alembic_tmp%'")).scalar() == 0

        insp = inspect(conn)
        assert "unit_hierarchy" not in {c["name"] for c in insp.get_columns("equipment")}
        assert {"unit_hierarchy", "unit_path"}.isdisjoint(
            {c["name"] for c in insp.get_columns("users")}
        )
        assert "unit_path" not in {c["name"] for c in insp.get_columns("locations")}
        # The two legacy columns H1-11 deliberately did NOT drop. They are the
        # only ones with a live reader, and H1-12 takes them with UserResponse.
        assert {"battalion", "company"} <= {c["name"] for c in insp.get_columns("users")}
    engine.dispose()


def test_the_backfill_leaves_rows_that_are_already_placed_alone(tmp_path):
    """The common shape in a real database, and the one the WHERE clause is for.

    Every item created through the API since H1-6 has a group_id and no path
    string -- create_equipment stopped writing one deliberately. Seeded items
    have both. So a live database is a mixture, and the backfill has to touch
    only the rows that need it.

    Without `WHERE group_id IS NULL` the correlated subquery returns NULL for
    any row whose path string is NULL, and an UPDATE would write that NULL
    straight over a perfectly good placement -- unplacing exactly the items the
    newest code created, and then refusing to migrate because of it. The
    refusal makes it loud rather than silent, which is the only reason this is
    a footgun and not a disaster.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'mixed.db'}")
    _upgrade(engine, GROUPS_REVISION)
    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO groups (id, name, kind) VALUES (4, '188/53/A', 'unit')")
        conn.exec_driver_sql("INSERT INTO groups (id, name, kind) VALUES (5, '188/53/B', 'unit')")
        conn.exec_driver_sql("INSERT INTO catalog_items (id, name) VALUES (1, 'M4')")
        # Created through the API: placed, and no path string.
        conn.exec_driver_sql(
            "INSERT INTO equipment (id, serial_number, catalog_item_id, group_id) "
            "VALUES (1, 'API-1', 1, 5)"
        )
        # Seeded before H1-4: a path string and no placement.
        conn.exec_driver_sql(
            "INSERT INTO equipment (id, serial_number, catalog_item_id, unit_hierarchy) "
            "VALUES (2, 'OLD-1', 1, '188/53/A')"
        )

    _upgrade(engine, "head")

    with engine.connect() as conn:
        placed = dict(conn.execute(text(
            "SELECT serial_number, group_id FROM equipment ORDER BY id"
        )).all())
    assert placed == {"API-1": 5, "OLD-1": 4}
    engine.dispose()


def test_migration_refuses_rather_than_stranding_an_unplaceable_item(tmp_path):
    """No group of that name, so the backfill cannot place it. Refuse.

    An item in no group is visible to no commander under the new model, and a
    migration that invented a placement to satisfy its own NOT NULL would be
    making an authority decision somewhere nobody would ever look for it --
    the SEC-H4 shape this phase exists to remove. Leaving the column nullable
    instead keeps the schema disagreeing with every write path.

    The schema must be untouched afterwards: the refusal is raised before the
    first DDL statement precisely so an operator is not left holding a table
    half-rebuilt by SQLite's drop-and-rename.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'orphan.db'}")
    _upgrade(engine, GROUPS_REVISION)
    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO groups (id, name, kind) VALUES (4, '188/53/B', 'unit')")
        _legacy_row(conn, unit_hierarchy="188/53/A")  # no group of that name

    with pytest.raises(RuntimeError) as excinfo:
        _upgrade(engine, "head")
    assert "SN-1" in str(excinfo.value), "the refusal must name the rows to fix"

    with engine.connect() as conn:
        insp = inspect(conn)
        assert "unit_hierarchy" in {c["name"] for c in insp.get_columns("equipment")}
        assert conn.execute(text("SELECT count(*) FROM equipment")).scalar() == 1
    engine.dispose()


def test_group_id_is_not_null_in_the_database_not_only_in_the_model(tmp_path):
    """The constraint has to be in the DDL, not just in the mapper.

    Every write path already refused to produce a NULL, which is why the
    schema and the model could disagree for four entries without anything
    failing. A raw INSERT is how a NULL would actually get in -- a fixture, a
    data import, a psql session -- and none of those go through the mapper.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'notnull.db'}")
    _upgrade(engine, "head")
    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO catalog_items (id, name) VALUES (1, 'M4')")
        with pytest.raises(IntegrityError):
            conn.exec_driver_sql(
                "INSERT INTO equipment (id, serial_number, catalog_item_id) "
                "VALUES (1, 'SN-X', 1)"
            )
    engine.dispose()


def test_the_downgrade_restores_the_shape_and_not_the_data(tmp_path):
    """Reversible for schema purposes, and honest about the rest.

    The columns come back empty and nullable; the path strings do not come
    back at all, and downgrade() says so rather than implying a restore. Worth
    pinning because an irreversible revision inside an otherwise reversible
    chain is discovered at the worst possible moment, and because the round
    trip is what proves the batch rebuild is symmetric.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'roundtrip.db'}")
    _upgrade(engine, GROUPS_REVISION)
    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO groups (id, name, kind) VALUES (4, '188/53/A', 'unit')")
        _legacy_row(conn)

    _upgrade(engine, "head")
    _downgrade(engine, GROUPS_REVISION)

    with engine.connect() as conn:
        insp = inspect(conn)
        assert "unit_hierarchy" in {c["name"] for c in insp.get_columns("equipment")}
        assert "unit_path" in {c["name"] for c in insp.get_columns("locations")}
        group_id = next(
            c for c in insp.get_columns("equipment") if c["name"] == "group_id"
        )
        assert group_id["nullable"] is True
        # The row survived both rebuilds. Its path string did not come back --
        # the backfill consumed it and the drop destroyed it.
        assert conn.execute(text(
            "SELECT unit_hierarchy, group_id FROM equipment WHERE id = 1"
        )).one() == (None, 4)
    engine.dispose()


def test_a_pre_alembic_database_is_stamped_where_the_migrations_can_reach_it(
    tmp_path, monkeypatch
):
    """The stamp target, which H1-11 is the first revision to care about.

    run_migrations() baselines a database that has tables but no
    alembic_version. It used to stamp "head" -- recording a version the
    database does not have, so every revision after the stamp is skipped in
    silence while the database reports itself current.

    Nothing noticed for two revisions because both were additive and such a
    database already had what they add. H1-11 drops columns, so a stamped
    database would keep them, keep a nullable group_id, and still say it was
    up to date. The stamp now names the revision whose schema the database
    actually has, and the rest of the chain runs.
    """
    legacy = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{legacy}")
    _upgrade(engine, GROUPS_REVISION)
    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO groups (id, name, kind) VALUES (4, '188/53/A', 'unit')")
        _legacy_row(conn)
        # Exactly the state the baseline path exists for: real tables, real
        # rows, and no record of ever having been migrated.
        conn.exec_driver_sql("DROP TABLE alembic_version")
    engine.dispose()

    engine = create_engine(f"sqlite:///{legacy}")
    monkeypatch.setattr(migrations, "engine", engine)
    migrations.run_migrations()

    with engine.connect() as conn:
        stamped = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert stamped != GROUPS_REVISION, (
            "stamped where the migrations stop, so nothing after it ever runs"
        )
        insp = inspect(conn)
        assert "unit_hierarchy" not in {c["name"] for c in insp.get_columns("equipment")}
        # The backfill ran on the way through rather than being skipped.
        assert conn.execute(text("SELECT group_id FROM equipment WHERE id = 1")).scalar() == 4
    engine.dispose()


def test_a_database_between_h1_11_and_h1_12_is_stamped_where_h1_12_can_reach_it(
    tmp_path, monkeypatch
):
    """The third shape, added by H1-12 -- and the one the first version of this
    entry's own fix got wrong.

    A database that already lost unit_hierarchy (H1-11 ran) but still carries
    the profiles table (H1-12 has not) exists whenever a create_all database
    is built from a models.py snapshot between the two revisions landing.
    Stamping it at "head" -- what baseline_revision did before this branch
    covered the shape -- skips H1-12 in silence while the database calls
    itself current, exactly the bug H1-11 fixed for its own revision. Caught
    by building this exact shape and running run_migrations() against it,
    not by reasoning about the function.
    """
    db = tmp_path / "between.db"
    engine = create_engine(f"sqlite:///{db}")
    _upgrade(engine, LEGACY_DROP_REVISION)
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE alembic_version")
    engine.dispose()

    engine = create_engine(f"sqlite:///{db}")
    monkeypatch.setattr(migrations, "engine", engine)
    migrations.run_migrations()

    with engine.connect() as conn:
        insp = inspect(conn)
        assert "profiles" not in insp.get_table_names()
        assert {"role", "profile_id", "battalion", "company"}.isdisjoint(
            {c["name"] for c in insp.get_columns("users")}
        )
        assert conn.execute(text("SELECT count(*) FROM alembic_version")).scalar() == 1
    engine.dispose()


def test_a_create_all_database_from_todays_models_is_already_at_head(
    tmp_path, monkeypatch
):
    """The other shape the baseline path has to survive.

    Fixing the stamp target is not simply "stamp earlier". A database built by
    create_all from the current models never had unit_hierarchy, so stamping it
    at the revision before H1-11 points that revision's backfill at a column
    that does not exist -- and it fails at startup with `no such column`.

    That is a real regression the first version of this fix introduced, caught
    by building the database rather than by reasoning about it. The stamp is
    chosen by inspecting the schema now, so both shapes land somewhere true.
    """
    db = tmp_path / "modern.db"
    engine = create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(bind=engine)
    engine.dispose()

    engine = create_engine(f"sqlite:///{db}")
    monkeypatch.setattr(migrations, "engine", engine)
    migrations.run_migrations()

    with engine.connect() as conn:
        insp = inspect(conn)
        assert "unit_hierarchy" not in {c["name"] for c in insp.get_columns("equipment")}
        assert conn.execute(
            text("SELECT count(*) FROM alembic_version")
        ).scalar() == 1
    engine.dispose()


def test_the_baseline_revision_is_chosen_by_what_the_schema_carries(tmp_path):
    """The marker itself, asserted directly on all three shapes.

    The run_migrations() tests above each exercise one branch end to end,
    which is the behaviour that matters -- but they would all still pass if
    the function returned the right answer for the wrong reason. This pins the
    rule for each: unit_hierarchy present means the oldest shape, profiles
    present (with it already gone) means the middle one, and neither present
    means the baseline stamp -- not head, because a schema that matches the
    models says nothing about whether a data-only revision has run
    (DATA-H5-2, and migrations.BASELINE_STAMP).

    The last branch is UNCONDITIONAL, and a create_all database is the shape
    that proves it has to be. Since DATA-H12 such a database carries check
    constraints that a pre-Alembic one built years ago does not, which makes it
    tempting to read them as evidence and stamp this shape later. The branch
    deliberately does not: see baseline_revision's docstring, and
    tests/test_verification_backfill.py for the database that carries the
    constraints and still needs the backfill.
    """
    legacy = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    _upgrade(legacy, GROUPS_REVISION)
    with legacy.connect() as conn:
        assert migrations.baseline_revision(inspect(conn)) == GROUPS_REVISION
    legacy.dispose()

    between = create_engine(f"sqlite:///{tmp_path / 'between.db'}")
    _upgrade(between, LEGACY_DROP_REVISION)
    with between.connect() as conn:
        assert migrations.baseline_revision(inspect(conn)) == LEGACY_DROP_REVISION
    between.dispose()

    modern = create_engine(f"sqlite:///{tmp_path / 'modern.db'}")
    Base.metadata.create_all(bind=modern)
    with modern.connect() as conn:
        assert migrations.baseline_revision(inspect(conn)) == migrations.BASELINE_STAMP
    modern.dispose()


def _drift_after(tmp_path, statement):
    """describe_drift's answer for a head database with one statement applied."""
    engine = create_engine(f"sqlite:///{tmp_path / 'drift.db'}")
    _upgrade(engine, "head")
    with engine.begin() as conn:
        conn.exec_driver_sql(statement)
    with engine.connect() as conn:
        drift = migrations.describe_drift(conn)
    engine.dispose()
    return drift


def test_a_missing_plain_index_is_not_drift(tmp_path):
    """DATA-H13-1. A database without an index is slow, not wrong.

    The day a revision adds an index, every pre-Alembic database lacks it, and
    the revision that would create it runs only after the stamp that
    describe_drift gates. Counting it refused all of them at startup -- which
    the run_migrations() tests above meet end to end wherever they build their
    legacy database from an old revision. This asserts the rule itself.
    """
    drift = _drift_after(tmp_path, "DROP INDEX ix_equipment_holder_user_id")

    assert drift == [], f"a missing non-unique index was counted as drift: {drift}"


@pytest.mark.parametrize(
    "statement, kind",
    [
        ("DROP INDEX ix_users_personal_number", "add_index"),
        ("ALTER TABLE fault_types DROP COLUMN severity", "add_column"),
        ("DROP TABLE daily_stats", "add_table"),
    ],
    ids=["unique_index", "column", "table"],
)
def test_what_a_query_or_a_rule_depends_on_is_still_drift(tmp_path, statement, kind):
    """The other half: the exemption above must be exactly as wide as it says.

    A unique index is a rule about the data, and a database without
    ix_users_personal_number accepts two accounts with one military ID. An
    exemption written as "ignore add_index" rather than "ignore a NON-UNIQUE
    add_index" would wave that through, and so would one that emptied the
    check altogether -- which is why a column and a table sit beside it.
    """
    drift = _drift_after(tmp_path, statement)

    assert [d[0] for d in drift] == [kind], drift


# Raw SQL that changes shape rather than rows. The op.* check below cannot see
# these, and this revision chain's data migrations establish
# conn.execute(text(...)) as their idiom -- so a "quick" ALTER through the same
# door is the likely way a schema change lands after the marker.
DDL_SQL = ("alter table", "create table", "drop table", "create index",
           "drop index", "add column", "drop column")


def schema_work_in_upgrade(source):
    """Every schema operation an Alembic revision's upgrade() performs.

    Two spellings, because a revision has two ways to reach the schema and a
    guard that knows only the tidy one is a guard against tidy mistakes.

    op.* is matched by PREFIX rather than against a list of DDL verbs: the whole
    of that module's surface is schema work, so a list would silently miss
    whichever verb a future revision reaches for. get_bind is the one member
    that touches no schema, and data revisions need it.

    Two limits, both known and neither worth more machinery than the risk:

    The op arm matches the NAME `op`, which every revision in this chain and
    every one Alembic's own template generates binds with `from alembic import
    op`. An alias -- `from alembic import op as o` -- would walk past it. That
    is an unenforced convention rather than a guarantee, so an author tidying
    the imports of a revision should know they are also tidying away its guard.

    The SQL arm can flag a string that merely CONTAINS one of these phrases,
    such as an error message naming the table it refuses to create. That fails
    in the safe direction -- a spurious red on a data revision, never a silent
    pass on a schema one -- so it is left blunt.
    """
    tree = ast.parse(source)
    upgrade = next(
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "upgrade"
    )

    found = []
    for node in ast.walk(upgrade):
        if not isinstance(node, ast.Call):
            continue

        if (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "op"
            and node.func.attr != "get_bind"
        ):
            found.append(f"op.{node.func.attr}()")

        # Strings PASSED TO a call, not every string in the body. A docstring
        # is an expression rather than an argument, which is what keeps this
        # off the prose -- these revisions discuss ALTER TABLE at length, and a
        # guard that flagged the discussion would be turned off within a week.
        for argument in ast.walk(node):
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                statement = " ".join(argument.value.lower().split())
                found += [phrase for phrase in DDL_SQL if phrase in statement]

    # Nested calls -- execute(text(...)) -- are walked twice, so the same
    # statement can arrive more than once. The offender list is read by a human
    # deciding whether to move a marker; saying it twice helps nobody.
    return list(dict.fromkeys(found))


def test_no_revision_after_the_schema_marker_touches_schema():
    """A revision after the stamp must move data, or survive being re-run.

    baseline_revision stamps a modern create_all database at BASELINE_STAMP, so
    every revision after it RUNS against a database that already has today's
    schema. That is automatically correct while those revisions only move data.
    A schema revision landing there re-applies DDL to a database that already
    has it, which is loud on Postgres and SILENT on SQLite -- batch_alter_table
    reflects the existing table and collapses a duplicate constraint into the
    original, so the suite stays green while a deployment breaks.

    DATA-H12 needed such a revision and could not avoid it: the chain already
    had a data-only revision at its head, so any new schema revision lands
    after the stamp. Moving the stamp instead is the one thing that must not
    happen -- it tells every legacy database DATA-H5-2's backfill already ran.
    So the escape is an explicit allowlist of revisions that tolerate a re-run,
    and this guard admits those and nothing else.

    The allowlist is not taken on trust: the test below applies each name in it
    twice and asserts the second run is a no-op. This one answers "is it
    allowed", that one answers "is it actually safe", and an entry needs both.

    Checked by walking the revisions rather than by trusting a comment, in the
    same spirit as tests/test_audit_trail.py's AST guards: the constant is
    kept honest by something that fails, not by a note asking for care.
    """
    script = ScriptDirectory.from_config(migrations.alembic_config())

    # walk_revisions yields newest first, so everything seen before the marker
    # is what a stamp at the marker skips past.
    newer = []
    found_marker = False
    for rev in script.walk_revisions("base", "heads"):
        if rev.revision == migrations.BASELINE_STAMP:
            found_marker = True
            break
        newer.append(rev)

    assert found_marker, (
        f"migrations.BASELINE_STAMP names {migrations.BASELINE_STAMP}, "
        "which is not a revision in the chain at all"
    )

    offenders = []
    for rev in newer:
        if rev.revision in migrations.IDEMPOTENT_SCHEMA_REVISIONS:
            continue
        source = Path(rev.module.__file__).read_text(encoding="utf-8")
        offenders += [f"{rev.revision}: {found}" for found in schema_work_in_upgrade(source)]

    assert offenders == [], (
        "a revision after migrations.BASELINE_STAMP changes schema, so a "
        "pre-Alembic database stamped there would be told it already has DDL "
        "it has never run. Either that revision must only move data, or it "
        "must tolerate being re-run and be named in "
        f"migrations.IDEMPOTENT_SCHEMA_REVISIONS: {offenders}"
    )


def test_every_allowlisted_revision_actually_survives_a_second_run(tmp_path):
    """The allowlist is a claim about behaviour, so check the behaviour.

    IDEMPOTENT_SCHEMA_REVISIONS lets a revision past the guard above. An entry
    added without the property it asserts would buy exactly the silent failure
    that guard exists to catch, and nothing else in the suite would notice --
    the re-run happens only on a database that already has the changes, which
    is not the shape the migration tests build.

    Applied twice against one database, asserting the second run EMITS NO DDL.
    Neither weaker check works, and finding that out took a mutation: on SQLite
    batch_alter_table reflects the existing table and collapses a duplicate
    constraint into the original, so re-running a revision with no skip at all
    raises nothing AND leaves the schema identical. This test asserted exactly
    those two things first, and a revision stripped of its skip sailed through
    it. What separates the two cases is whether the work is DONE again, not
    whether the result differs -- and on Postgres that same re-run is a
    DuplicateObject at startup, which no test in this suite would reach.

    Deliberately not parametrized over the set: the whole set has to run in
    CHAIN order against one database, so a later revision meets the state an
    earlier one leaves. `sorted()` would not give that -- revision ids are
    hex and sort lexicographically, which has nothing to do with the chain --
    so the order comes from walk_revisions, reversed to run oldest first.
    """
    assert migrations.IDEMPOTENT_SCHEMA_REVISIONS, (
        "nothing is allowlisted, so this test is asserting nothing -- delete it "
        "together with the allowlist rather than leaving it to look like cover"
    )

    script = ScriptDirectory.from_config(migrations.alembic_config())
    in_chain_order = allowlisted_in_chain_order(script)
    unknown = migrations.IDEMPOTENT_SCHEMA_REVISIONS - set(in_chain_order)
    assert unknown == set(), (
        f"IDEMPOTENT_SCHEMA_REVISIONS names revisions not in the chain: {sorted(unknown)}"
    )

    engine = create_engine(f"sqlite:///{tmp_path / 'twice.db'}")
    _upgrade(engine, "head")
    before = _schema_snapshot(engine)

    with recorded(engine) as statements:
        for revision in in_chain_order:
            with engine.begin() as conn:
                _rerun_upgrade(conn, script, revision)

    after = _schema_snapshot(engine)
    engine.dispose()

    ddl = [
        s for s in statements
        if any(
            verb in s.upper()
            for verb in ("CREATE TABLE", "ALTER TABLE", "DROP TABLE", "CREATE INDEX", "DROP INDEX")
        )
    ]
    assert ddl == [], (
        "re-running an allowlisted revision emitted DDL against a database that "
        "already has its changes. SQLite absorbs that; Postgres raises at "
        f"startup. The revision needs to detect its own work and skip it: {ddl}"
    )
    assert after == before, (
        "re-running an allowlisted revision changed the schema, so it is not "
        f"idempotent: {sorted(k for k in after if after[k] != before.get(k))}"
    )


def test_the_migration_refuses_rows_outside_the_vocabulary(tmp_path):
    """The pre-flight guard, fired on purpose. DATA-H12.

    Postgres validates existing rows as part of ADD CONSTRAINT, so without this
    check a database holding one bad status fails the upgrade with a constraint
    name and no row attached to it. The transaction rolls back either way; what
    the pre-flight buys is a message naming the rows, and naming all of them.

    Written because the guard survived a mutation otherwise: deleting the
    refusal entirely left the whole suite green, since every other migration
    test builds its rows through code that already writes enum members. A guard
    nothing fires is a guard nothing is holding.

    Two bad rows in two different tables, asserted together, because the
    revision collects every violation before raising rather than stopping at
    the first -- an operator who has to re-run once per bad row is the other
    failure this avoids.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'dirty.db'}")
    _upgrade(engine, CONSTRAINTS_REVISION_PARENT)

    with engine.begin() as conn:
        conn.execute(text("INSERT INTO groups (id, name, kind) VALUES (1, '188', 'UNIT')"))
        conn.execute(text("INSERT INTO catalog_items (id, name) VALUES (1, 'M4')"))
        conn.execute(text(
            "INSERT INTO equipment (id, catalog_item_id, group_id, status, serial_number)"
            " VALUES (7, 1, 1, 'Functinoal', 'SN-BAD')"
        ))
        conn.execute(text(
            "INSERT INTO equipment (id, catalog_item_id, group_id, status, serial_number)"
            " VALUES (8, 1, 1, 'Functional', 'SN-OK')"
        ))
        conn.execute(text(
            "INSERT INTO maintenance_logs (id, equipment_id, status) VALUES (3, 8, 'Pending')"
        ))

    before = _schema_snapshot(engine)
    with pytest.raises(RuntimeError) as excinfo:
        _upgrade(engine, "head")
    message = str(excinfo.value)
    after = _schema_snapshot(engine)
    engine.dispose()

    assert "equipment.status #7" in message and "Functinoal" in message, message
    assert "maintenance_logs.status #3" in message and "Pending" in message, (
        "only the first table's violations were reported, so an operator learns "
        f"about the rest one re-run at a time: {message}"
    )
    assert "SN-OK" not in message and "#8" not in message, (
        f"a row holding a valid status was named as a violation: {message}"
    )
    assert after == before, "the refusal changed the schema; it must change nothing"


def test_a_legacy_row_holding_null_does_not_block_the_upgrade(tmp_path):
    """The refusal's other half: what it must NOT stop.

    equipment.status is nullable and a CHECK is satisfied by NULL, so a legacy
    database carrying NULLs is compliant with the new constraint and has to be
    able to migrate. A pre-flight written to refuse "anything that is not a
    member" rather than "anything that is a non-member" would refuse exactly
    those databases -- turning a constraint nobody violates into an upgrade
    nobody can run, discovered on the deploy rather than here.

    This is the false-positive companion to the test above, and the pair is the
    point: one shows the guard fires, this shows it fires only when it should.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'nulls.db'}")
    _upgrade(engine, CONSTRAINTS_REVISION_PARENT)

    with engine.begin() as conn:
        conn.execute(text("INSERT INTO groups (id, name, kind) VALUES (1, '188', 'UNIT')"))
        conn.execute(text("INSERT INTO catalog_items (id, name) VALUES (1, 'M4')"))
        conn.execute(text(
            "INSERT INTO equipment (id, catalog_item_id, group_id, status, sensitivity)"
            " VALUES (1, 1, 1, NULL, NULL)"
        ))

    _upgrade(engine, "head")

    with engine.connect() as conn:
        survived = conn.execute(text("SELECT status, sensitivity FROM equipment")).one()
    names = {c["name"] for c in inspect(engine).get_check_constraints("equipment")}
    engine.dispose()

    assert survived == (None, None), "the migration rewrote a NULL it was asked to leave alone"
    assert "ck_equipment_status" in names, "the upgrade did not actually run"


def allowlisted_in_chain_order(script):
    """migrations.IDEMPOTENT_SCHEMA_REVISIONS, oldest revision first.

    walk_revisions yields newest first, so this reverses it. Shared with the
    Postgres half of this check in tests/test_utc_migration_postgres.py, which
    needs the same ordering for the same reason and must not re-derive it.
    """
    chain = [rev.revision for rev in script.walk_revisions("base", "heads")]
    chain.reverse()
    return [r for r in chain if r in migrations.IDEMPOTENT_SCHEMA_REVISIONS]


def _rerun_upgrade(conn, script, revision):
    """Run one revision's upgrade() again, against an already-migrated database.

    Alembic will not replay an applied revision through `command.upgrade`, and
    that is the whole situation being tested: baseline_revision hands a legacy
    database a stamp that predates this revision, so Alembic runs it against a
    schema that may already carry its changes. Driving upgrade() directly with
    a MigrationContext bound to the live connection reproduces exactly that,
    without faking the version table.
    """
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    context = MigrationContext.configure(conn)
    with Operations.context(context):
        script.get_revision(revision).module.upgrade()


PLANTED_SCHEMA_WORK = {
    "op call": "    op.add_column('equipment', sa.Column('x', sa.String()))",
    "batch mode": (
        "    with op.batch_alter_table('equipment') as b:\n"
        "        b.drop_column('x')"
    ),
    "raw sql": '    conn.execute(text("ALTER TABLE equipment ADD COLUMN x VARCHAR"))',
    "raw sql wrapped over lines": (
        '    conn.execute(text(\n'
        '        "CREATE INDEX ix"\n'
        '        " ON equipment (id)"\n'
        '    ))'
    ),
}


@pytest.mark.parametrize("spelling", sorted(PLANTED_SCHEMA_WORK), ids=lambda s: s.replace(" ", "_"))
def test_the_schema_marker_guard_sees_every_spelling(spelling):
    """The guard asserts == [] against a chain that has no violation to find.

    So on the real tree it passes whether or not it can detect anything, which
    is a detector nobody has watched detect -- the trap
    tests/test_audit_trail.py documents for its own guards. These plant one.

    The wrapped case is the one worth spelling out: adjacent string literals
    concatenate at parse time, so "CREATE INDEX ix" " ON equipment (id)" is one
    constant by the time the walk sees it, and a check reading raw source lines
    would miss it.
    """
    source = f"def upgrade():\n{PLANTED_SCHEMA_WORK[spelling]}\n"

    assert schema_work_in_upgrade(source), (
        f"a {spelling} schema change in upgrade() was not seen by the guard"
    )


def test_the_schema_marker_guard_ignores_a_data_only_revision():
    """The other half: prose about DDL, and row work, must not be flagged.

    This revision chain's comments discuss ALTER TABLE at length, so a guard
    reading whole files rather than upgrade() bodies would flag the very
    revisions it is meant to allow.
    """
    source = (
        'def upgrade():\n'
        '    """Rewrites rows. Not an ALTER TABLE, which would need op.alter_column()."""\n'
        '    conn = op.get_bind()\n'
        '    conn.execute(text("UPDATE equipment SET last_verified_at = NULL"))\n'
    )

    assert schema_work_in_upgrade(source) == []


def _schema_snapshot(engine):
    """Everything about a schema that two ways of building it must agree on.

    Column ORDER is excluded: ALTER TABLE ADD COLUMN always appends, so a
    migrated `equipment` carries group_id last while create_all places it as
    declared. That difference is unavoidable and harmless to a named-column ORM.

    CHECK CONSTRAINTS are included as of DATA-H12, and that is what makes an
    enum and a migration unable to drift apart quietly. The models generate
    their constraint text from backend/enums.py; a revision freezes its
    literals. Add a member to EquipmentStatus without writing a revision and
    create_all starts emitting a vocabulary `head` does not -- this comparison
    is the only thing that notices. Expect this test, not a failing feature, to
    be what tells you a migration is missing.

    The text is compared with whitespace collapsed and nothing else normalized.
    Quoting and case are left alone on purpose: they are what carries the
    vocabulary, so normalizing them would keep the constraint's SHAPE pinned
    while letting its MEANING drift, which is the opposite of the point. This
    only works because both sides here are SQLite, which reflects the literal
    source text -- Postgres answers through pg_get_constraintdef and rewrites
    `IN (...)` as `= ANY (ARRAY[...])`, so anything comparing across dialects
    must match on constraint NAME alone.
    """
    insp = inspect(engine)
    out = {}
    for table in insp.get_table_names():
        if table == "alembic_version":
            continue
        out[table] = {
            "columns": sorted((c["name"], str(c["type"]), c["nullable"]) for c in insp.get_columns(table)),
            # options carries ondelete/onupdate, and the constraint name is
            # what downgrade()'s drop_constraint targets on Postgres.
            # Comparing only columns/referred_table would let the migration
            # lose every ondelete rule with the suite still green -- the one
            # promise H1-1 leads with, unpinned on the side that ships.
            "foreign_keys": sorted(
                (
                    tuple(fk["constrained_columns"]),
                    fk["referred_table"],
                    tuple(fk["referred_columns"]),
                    fk.get("name"),
                    tuple(sorted((fk.get("options") or {}).items())),
                )
                for fk in insp.get_foreign_keys(table)
            ),
            "indexes": sorted((i["name"], tuple(i["column_names"]), i["unique"]) for i in insp.get_indexes(table)),
            "pk": tuple(insp.get_pk_constraint(table)["constrained_columns"]),
            "unique": sorted(
                (u.get("name"), tuple(u["column_names"])) for u in insp.get_unique_constraints(table)
            ),
            # Unnamed constraints are dropped rather than compared: SQLite
            # reflects them as name=None with no stable identity to line up
            # across two databases. Everything this repository declares is
            # named, and models._one_of is why -- an unnamed CHECK would also
            # be invisible to the revision's own by-name skip.
            "checks": sorted(
                (c["name"], " ".join(c["sqltext"].split()))
                for c in insp.get_check_constraints(table)
                if c.get("name")
            ),
        }
    return out


def test_migration_and_create_all_build_the_same_schema(tmp_path):
    """The suite builds its schema with create_all; CI and production migrate.

    Any divergence means tests pass against a schema that is not the one that
    ships. What is compared, and what is deliberately not, is in
    _schema_snapshot.
    """
    migrated = create_engine(f"sqlite:///{tmp_path / 'migrated.db'}")
    _upgrade(migrated, "head")

    created = create_engine(f"sqlite:///{tmp_path / 'created.db'}")
    Base.metadata.create_all(bind=created)

    a, b = _schema_snapshot(migrated), _schema_snapshot(created)
    migrated.dispose()
    created.dispose()

    assert set(a) == set(b), f"table sets differ: {set(a) ^ set(b)}"
    differing = {t: (a[t], b[t]) for t in a if a[t] != b[t]}
    assert differing == {}, f"migrated and create_all schemas diverge: {sorted(differing)}"
