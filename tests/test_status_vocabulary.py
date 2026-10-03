"""DATA-H12: the database refuses a status or classification it does not know.

Every one of these columns already held a vocabulary. What it did not have was
anything enforcing one -- `Column(String)` accepts 'Functinoal' as readily as
'Functional', and analytics.unit_readiness counts readiness by matching one
exact literal, so a single typo lowers a unit's reported readiness with nothing
anywhere reporting an error.

THESE TESTS GO THROUGH RAW SQL, NOT THROUGH A ROUTE, and that is the whole
design rather than a shortcut. No application write path produces a value
outside these vocabularies -- some pass enum members, and the few that still
write bare literals (routers/maintenance.py's "Open" and "Closed",
seed_data.py's "Functional") write strings that happen to spell members, which
DATA-H12-2 sources from the enums. So a test driving the API would pass just as
well against the unconstrained columns: it would be pinning the callers' good
behaviour rather than the database's refusal. Hand-written SQL is the reachable
hole this ticket closes, so hand-written SQL is what these tests use.
"""
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend import models
from backend.database import Base
from backend.enums import ChangeReason, EquipmentStatus, Sensitivity, TicketStatus

# (table, column, constraint name, the enum the column carries)
CONSTRAINED = (
    ('equipment', 'status', 'ck_equipment_status', EquipmentStatus),
    ('equipment', 'sensitivity', 'ck_equipment_sensitivity', Sensitivity),
    ('maintenance_logs', 'status', 'ck_maintenance_logs_status', TicketStatus),
    ('verifications', 'reported_status', 'ck_verifications_reported_status', EquipmentStatus),
    ('equipment_status_history', 'old_status',
     'ck_equipment_status_history_old_status', EquipmentStatus),
    ('equipment_status_history', 'new_status',
     'ck_equipment_status_history_new_status', EquipmentStatus),
    ('equipment_status_history', 'change_reason',
     'ck_equipment_status_history_change_reason', ChangeReason),
)

# The columns a row needs before it can be inserted at all, so that what fails
# below is the CHECK and never a NOT NULL sitting in front of it.
ROW_TEMPLATE = {
    'equipment': {'catalog_item_id': 1, 'group_id': 1},
    'maintenance_logs': {'equipment_id': 1},
    'verifications': {
        'equipment_id': 1,
        'verification_type': 'ROUTINE',
        'reported_status': EquipmentStatus.FUNCTIONAL.value,
        'created_by': 1,
    },
    'equipment_status_history': {
        'equipment_id': 1,
        'old_status': EquipmentStatus.FUNCTIONAL.value,
        'new_status': EquipmentStatus.FUNCTIONAL.value,
        'change_reason': 'verification',
        'created_by': 1,
    },
}

# The columns a CHECK leaves free to be absent. maintenance_logs.status is the
# easy one to miss -- it has a default, which reads like a guarantee and is
# not one: a Python-side default is skipped entirely by raw SQL.
NULLABLE = {
    ('equipment', 'status'),
    ('equipment', 'sensitivity'),
    ('maintenance_logs', 'status'),
}


@pytest.fixture
def schema(tmp_path):
    """A real database built from the models, with foreign keys NOT enforced.

    Its own engine rather than the suite's, because these tests insert by raw
    SQL and the conftest session hands out an in-memory database shared with
    the app.

    Foreign keys are left unenforced deliberately: every row below names parent
    ids that do not exist, because the parents are irrelevant to what is being
    asserted and creating them would put four more tables between the test and
    the constraint it is about. A CHECK is evaluated regardless.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'vocab.db'}")
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


def _insert(engine, table, **overrides):
    row = dict(ROW_TEMPLATE[table], **overrides)
    columns = ", ".join(row)
    placeholders = ", ".join(f":{c}" for c in row)
    with engine.begin() as conn:
        conn.execute(text(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"), row)


@pytest.mark.parametrize(
    "table,column,name,values",
    CONSTRAINED,
    ids=[f"{t}.{c}" for t, c, _n, _v in CONSTRAINED],
)
def test_the_constraint_is_declared_on_the_column(schema, table, column, name, values):
    """A roll-call, so deleting one _one_of line fails here and names it.

    The behavioural tests below would also go red, but they would go red
    describing an accepted value rather than a missing constraint, which is a
    slower way to learn the same thing.
    """
    declared = {c["name"] for c in inspect(schema).get_check_constraints(table)}
    assert name in declared, (
        f"{table}.{column} carries no {name}; it accepts any string again"
    )


@pytest.mark.parametrize(
    "table,column,member",
    [
        (t, c, member)
        for t, c, _n, values in CONSTRAINED
        for member in values
    ],
    ids=[
        f"{t}.{c}={member.value}"
        for t, c, _n, values in CONSTRAINED
        for member in values
    ],
)
def test_every_member_of_the_vocabulary_is_accepted(schema, table, column, member):
    """Each member individually, not one representative value.

    A constraint listing three of the four statuses passes any test that only
    tries 'Functional'. The value that would be refused is exactly the rare one
    -- 'In Repair' and 'Missing' are written by the verification form and by no
    backend comparison -- so the narrow case is the one a single-value test
    misses.
    """
    _insert(schema, table, **{column: member.value})


@pytest.mark.parametrize(
    "table,column,name,values",
    CONSTRAINED,
    ids=[f"{t}.{c}" for t, c, _n, _v in CONSTRAINED],
)
def test_a_value_outside_the_vocabulary_is_refused(schema, table, column, name, values):
    """The defect itself: a typo used to be a valid persisted value."""
    typo = next(iter(values)).value.upper() + "_XX"
    with pytest.raises(IntegrityError) as excinfo:
        _insert(schema, table, **{column: typo})

    assert name in str(excinfo.value), (
        f"the write was refused, but not by {name} -- check what actually "
        f"rejected it before believing this test: {excinfo.value}"
    )


@pytest.mark.parametrize(
    "table,column",
    sorted(NULLABLE),
    ids=[f"{t}.{c}" for t, c in sorted(NULLABLE)],
)
def test_null_is_still_accepted_where_the_column_is_nullable(schema, table, column):
    """Deliberate, and pinned so it is not mistaken for an oversight.

    A CHECK is satisfied by NULL in SQL, so this ticket constrains WHICH string
    a column may hold and says nothing about whether it must hold one. Making
    these NOT NULL needs a decision about what to backfill existing NULLs with,
    which is DATA-M12's, not this ticket's.

    audit_trail.set_status already refuses a NULL status with a 409 rather than
    laundering it, so the application does not depend on the column for this.
    """
    _insert(schema, table, **{column: None})


@pytest.mark.parametrize(
    "spelling",
    ["functional", "FUNCTIONAL", "Functional ", " Functional", "Functional\n", ""],
    ids=["lowercase", "uppercase", "trailing_space", "leading_space", "newline", "empty"],
)
def test_a_near_miss_spelling_is_refused_like_any_other(schema, spelling):
    """The typos that actually happen, rather than an obviously invented one.

    A constraint written with a case-insensitive collation, or one that trimmed
    its input, would accept most of these and quietly create a second spelling
    of a status the readiness count does not match -- which is the original
    defect wearing a tidier disguise. SQLite compares with BINARY collation by
    default and Postgres compares varchar exactly, so both refuse; this pins
    that nobody "helpfully" relaxes it later.

    The empty string is in the list because it is the falsy corruption
    audit_trail guards against in application code (see set_status). It is not
    a NULL and gets no special treatment from a CHECK -- it is simply a string
    that is not in the vocabulary.
    """
    with pytest.raises(IntegrityError):
        _insert(schema, "equipment", status=spelling)


def test_the_constraint_applies_to_updates_and_not_only_inserts(schema):
    """The realistic corruption path is an UPDATE, so assert that one directly.

    Every other test here inserts. A constraint that somehow only covered
    inserts would leave the actual hole this ticket closes wide open: the way a
    bad status gets into a real database is somebody repairing a row by hand,
    not somebody inventing one.
    """
    _insert(schema, "equipment", status=EquipmentStatus.FUNCTIONAL.value)
    with pytest.raises(IntegrityError) as excinfo, schema.begin() as conn:
        conn.execute(text("UPDATE equipment SET status = 'Broken'"))

    assert "ck_equipment_status" in str(excinfo.value)


@pytest.mark.parametrize(
    "build,column",
    [
        (lambda: models.Equipment(catalog_item_id=1, group_id=1), "status"),
        (lambda: models.Equipment(catalog_item_id=1, group_id=1), "sensitivity"),
        (lambda: models.MaintenanceLog(equipment_id=1), "status"),
    ],
    ids=["equipment.status", "equipment.sensitivity", "maintenance_logs.status"],
)
def test_the_column_default_satisfies_its_own_constraint(schema, build, column):
    """A default and a constraint are two places the vocabulary is written down.

    Both are generated from the same enum today, so they cannot disagree by
    accident -- but a default is a plain value and nothing else would notice it
    edited to a string the constraint refuses. The failure mode is the worst
    kind: every ordinary create starts raising IntegrityError, at runtime only.

    THROUGH THE ORM, not raw SQL, and that distinction is the whole test. These
    are Python-side defaults with no server_default behind them, so a raw
    INSERT omitting the column stores NULL -- which a CHECK accepts. Written
    the other way first, this test passed against a default of 'BANANA',
    measured rather than reasoned about.

    The not-None assertion is what stops it going quiet again if the default is
    ever removed: a missing default is also a NULL, and also accepted.
    """
    row = build()
    with Session(schema) as session:
        session.add(row)
        session.commit()
        stored = getattr(row, column)

    assert stored is not None, (
        f"{column} has no default any more, so this test no longer exercises one"
    )


@pytest.mark.parametrize(
    "enum_class",
    # Every enum _one_of is called with, ChangeReason included -- it was the one
    # left out when this list was first written, and it is the one that most
    # needs covering: the others are fixed UI vocabularies, while these are
    # lowercase English written for readers, where an apostrophe is a plausible
    # future value rather than a contrived one.
    [EquipmentStatus, Sensitivity, TicketStatus, ChangeReason],
    ids=lambda e: e.__name__,
)
def test_every_value_is_safe_to_inline_in_ddl(enum_class):
    """models._one_of interpolates, because DDL cannot take bind parameters.

    That makes the spelling of a member value load-bearing in a way it is not
    anywhere else: a value containing an apostrophe would close the SQL string
    early and emit a broken CHECK, and the failure would arrive at create_all
    or migration time rather than here. Cheap to prevent, confusing to debug.
    """
    for member in enum_class:
        assert "'" not in member.value and "\\" not in member.value, (
            f"{enum_class.__name__}.{member.name} = {member.value!r} cannot be "
            "inlined into a CHECK constraint; models._one_of would emit broken SQL"
        )
