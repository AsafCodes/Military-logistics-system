"""constrain status and sensitivity vocabularies

DATA-H12-1. Seven columns held a vocabulary and admitted any string.

analytics.unit_readiness counts readiness by matching 'Functional' exactly, so
a single typo -- 'Functinoal' on one item -- silently lowers the reported
readiness of a unit with nothing anywhere reporting an error. Every write path
already passes an enum member; this makes that true by construction rather than
by every caller remembering, which is what backend/enums.py has named this
ticket for since DATA-H3-1.

NULL IS STILL ACCEPTED wherever the column allows it -- equipment.status,
equipment.sensitivity and maintenance_logs.status. A CHECK is satisfied by
NULL, so this constrains WHICH string a column may hold, not whether it must
hold one. DATA-M12 owns non-null constraints. The remaining four columns are
already NOT NULL for reasons of their own.

THIS REVISION IS IDEMPOTENT, AND THAT IS LOAD-BEARING rather than tidiness.
It is the first SCHEMA revision to land after a DATA-ONLY one (6f821fc450b8,
DATA-H5-2's backfill), and those two facts cannot both be served by a single
stamp. backend.migrations.baseline_revision stamps a pre-Alembic database
before the backfill so the backfill still reaches it -- which means this
revision also runs against databases that already have these constraints,
because backend/models.py emits them from create_all. So it checks for each
constraint by name and skips the ones already present. See
backend.migrations.BASELINE_STAMP for the whole arrangement; removing the skip
below reintroduces a duplicate-name failure that is loud on Postgres and
SILENT on SQLite, where batch_alter_table reflects the existing table and
collapses the second constraint into the first.

BY NAME, NEVER BY TEXT. Postgres reflects a check constraint through
pg_get_constraintdef, which rewrites `col IN ('a', 'b')` as
`((col)::text = ANY ((ARRAY['a'::character varying, ...])::text[]))`. SQLite
reflects the literal source text. Comparing the two would make this revision
re-apply itself forever on one dialect.

THE LITERALS BELOW ARE FROZEN COPIES of backend/enums.py as it stood when this
was written, and are deliberately not imported from it. A revision records what
it did, not what the models currently say; importing the enum would silently
rewrite this revision's meaning the day somebody adds a member. The drift that
creates is caught rather than ignored --
tests/test_group_schema.py::test_migration_and_create_all_build_the_same_schema
compares a migrated schema against a create_all one and goes red when a new
member lands without a new revision.

Revision ID: d3a9c17be540
Revises: 6f821fc450b8
Create Date: 2026-09-19

"""
from sqlalchemy import inspect, text

from alembic import op

# revision identifiers, used by Alembic.
revision = 'd3a9c17be540'
down_revision = '6f821fc450b8'
branch_labels = None
depends_on = None


EQUIPMENT_STATUS = ('Functional', 'Malfunctioning', 'In Repair', 'Missing')
SENSITIVITY = ('UNCLASSIFIED', 'CLASSIFIED')
TICKET_STATUS = ('Open', 'In Progress', 'Waiting for Parts', 'Closed')
# Lowercase, unlike every other vocabulary here. ChangeReason's own docstring
# says that is deliberate, and it is exactly the kind of difference a frozen
# copy exists to preserve: a well-meaning tidy to match the others would
# invalidate every row already written.
CHANGE_REASON = ('verification', 'fault_report', 'repair')

# (table, column, constraint name, allowed values)
CONSTRAINTS = (
    ('equipment', 'status', 'ck_equipment_status', EQUIPMENT_STATUS),
    ('equipment', 'sensitivity', 'ck_equipment_sensitivity', SENSITIVITY),
    ('maintenance_logs', 'status', 'ck_maintenance_logs_status', TICKET_STATUS),
    ('verifications', 'reported_status', 'ck_verifications_reported_status', EQUIPMENT_STATUS),
    ('equipment_status_history', 'old_status',
     'ck_equipment_status_history_old_status', EQUIPMENT_STATUS),
    ('equipment_status_history', 'new_status',
     'ck_equipment_status_history_new_status', EQUIPMENT_STATUS),
    ('equipment_status_history', 'change_reason',
     'ck_equipment_status_history_change_reason', CHANGE_REASON),
)


def _sql_list(values):
    """The values as a SQL literal list. DDL cannot take bind parameters."""
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


def _existing_constraint_names(inspector, table):
    return {c.get('name') for c in inspector.get_check_constraints(table)}


def upgrade():
    conn = op.get_bind()
    inspector = inspect(conn)

    missing = [
        (table, column, name, values)
        for table, column, name, values in CONSTRAINTS
        if name not in _existing_constraint_names(inspector, table)
    ]
    if not missing:
        return

    # EVERY check before ANY change, and all violations in one message rather
    # than the first one found. Postgres validates existing rows as part of ADD
    # CONSTRAINT, so without this the failure an operator meets is a constraint
    # name from the server with no row attached to it. The upgrade runs in one
    # transaction and both dialects roll DDL back, so nothing is left half
    # applied either way -- what this buys is a message naming the rows, and
    # naming ALL of them, instead of an operator learning about them one
    # failed run at a time.
    violations = []
    for table, column, _name, values in missing:
        rows = conn.execute(text(
            f"SELECT id, {column} FROM {table} "
            f"WHERE {column} IS NOT NULL AND {column} NOT IN ({_sql_list(values)}) "
            f"ORDER BY id"
        )).all()
        violations += [
            f"{table}.{column} #{row[0]} holds {row[1]!r}" for row in rows
        ]

    if violations:
        raise RuntimeError(
            "Refusing to migrate: these rows hold a value outside the "
            "vocabulary their column is about to be constrained to, so adding "
            "the constraint would fail:\n  "
            + "\n  ".join(violations)
            + "\n\nCorrect each value to one the column accepts, then re-run. "
            "The accepted values are listed in backend/enums.py. Nothing has "
            "been changed."
        )

    for table, column, name, values in missing:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.create_check_constraint(name, f"{column} IN ({_sql_list(values)})")


def downgrade():
    conn = op.get_bind()
    inspector = inspect(conn)

    for table, _column, name, _values in reversed(CONSTRAINTS):
        if name not in _existing_constraint_names(inspector, table):
            continue
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_constraint(name, type_='check')
