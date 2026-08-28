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
import sqlite3

import pytest
from sqlalchemy import UniqueConstraint, create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from backend import authz, database, migrations, models
from backend.database import Base
from backend.enums import Capability, GroupKind

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

NEW_TABLES = ("groups", "group_edges", "group_closure", "group_memberships", "grants")


def _upgrade(engine, revision):
    """Migrate `engine` to `revision`, the same way the application does.

    Reuses backend.migrations.alembic_config() so the ini location and the
    logger guard are not re-derived here -- a second copy would let the suite
    drift from what production runs, which is the very divergence
    test_migration_and_create_all_build_the_same_schema exists to catch.

    begin(), not connect(): Alembic runs inside the caller's transaction when
    handed a connection, so the alembic_version write needs a commit at exit
    (backend/migrations.py:59).
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


def test_every_new_foreign_key_column_is_covered_by_an_index():
    """Otherwise every scoped join degrades to a scan as the tables grow.

    "Covered" means usable as a leading column, not necessarily owning a
    dedicated index: a column that leads the composite primary key or a
    composite unique constraint is already served by that index, and adding a
    second single-column index on it would be a redundant prefix.
    """
    uncovered = []
    for table_name in NEW_TABLES:
        table = Base.metadata.tables[table_name]

        leading = {next(iter(table.primary_key.columns)).name}
        leading |= {
            next(iter(c.columns)).name
            for c in table.constraints
            if isinstance(c, UniqueConstraint) and len(c.columns) > 0
        }
        indexed = {c.name for idx in table.indexes for c in idx.columns}
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


def test_the_application_enforces_foreign_keys_and_migrations_do_not():
    """The two engines must disagree, deliberately and in this direction.

    The application and the suite run under enforcement so declared cascades
    fire. Migrations must not: Alembic's batch_alter_table rebuilds a table by
    dropping and renaming it, and DROP TABLE equipment fails under enforcement
    because four tables reference it. The pragma cannot be toggled off for the
    duration either -- SQLite ignores it inside a transaction and says nothing
    -- so the separation has to be two engines. See create_database_engine.
    """
    for engine, expected in ((database.engine, 1), (migrations.engine, 0)):
        with engine.connect() as conn:
            assert conn.execute(text("PRAGMA foreign_keys")).scalar() == expected


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
    means head.
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
        assert migrations.baseline_revision(inspect(conn)) == "head"
    modern.dispose()


def test_migration_and_create_all_build_the_same_schema(tmp_path):
    """The suite builds its schema with create_all; CI and production migrate.

    Any divergence means tests pass against a schema that is not the one that
    ships. Column ORDER is excluded: ALTER TABLE ADD COLUMN always appends, so
    a migrated `equipment` carries group_id last while create_all places it as
    declared. That difference is unavoidable and harmless to a named-column ORM.
    """
    migrated = create_engine(f"sqlite:///{tmp_path / 'migrated.db'}")
    _upgrade(migrated, "head")

    created = create_engine(f"sqlite:///{tmp_path / 'created.db'}")
    Base.metadata.create_all(bind=created)

    def snapshot(engine):
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
            }
        return out

    a, b = snapshot(migrated), snapshot(created)
    migrated.dispose()
    created.dispose()

    assert set(a) == set(b), f"table sets differ: {set(a) ^ set(b)}"
    differing = {t: (a[t], b[t]) for t in a if a[t] != b[t]}
    assert differing == {}, f"migrated and create_all schemas diverge: {sorted(differing)}"
