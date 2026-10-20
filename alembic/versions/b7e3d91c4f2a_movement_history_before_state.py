"""give the movement history a before state, a reason and a vocabulary

DATA-M8-1. transaction_logs recorded who acted and, as one string in
`location`, where the item ended up -- a place, or "User:<name>" for a person.
Nothing recorded where it came from, nothing recorded why, and event_type was
a nullable string with no constraint. This revision adds:

- eight custody columns, from_ and to_ for each of holder, owner (user ids),
  group (a group id) and location (text), with RESTRICT foreign keys and an
  index on each id column (from_group_id's was replaced by a composite in
  3c6a0f8e2d71, DATA-M9-2; see SUPERSEDED below);
- a free-text `reason`;
- a CHECK holding event_type to the EventType vocabulary, and NOT NULL.

It drops involved_location_id, which nothing has ever written (DATA-M18), with
its foreign key and its index.

NO BACKFILL. Rows written before this revision keep NULL in every new column.
Their `location` strings name a person by full_name, which is not unique, so
an id recovered from one would be a guess -- and a guessed before state in an
audit trail is worse than an empty one. `location` itself stays, and keeps
those rows' after state.

TWO OLD SPELLINGS ARE RENAMED, everything else outside the vocabulary is
refused. The root main.py that the first Docker image ran (c48c56d, and again
after the revert 88cd4b6) wrote a fault report as 'FAULT_REPORT' and a repair
as 'REPAIR'. Those are the events EventType spells FAULT and FIX -- the same
two routes, the same acts -- so they become those values, and nothing else
about the row changes. The downgrade does not rename them back: both
spellings mean what the new ones mean, and nothing reads the old ones.

Anything else -- NULL, or a value nobody wrote -- is not renamed. If any row
holds one, nothing is changed, renames included, and the error names every
such row: the shape d3a9c17be540 set. Nothing the application wrote should
be refused: every event_type literal ever committed under backend/ is a
member (HANDOVER, HANDOVER_LOC, VERIFICATION, FIX), and since DATA-H4-1 the one
writer, audit_trail.record_event, takes an EventType.

THIS REVISION IS IDEMPOTENT, for the reason every schema revision after
backend.migrations.BASELINE_STAMP is: it also runs against create_all
databases that already have all of it. Each piece is checked by name -- a
column, a foreign key, an index, the CHECK -- and NOT NULL by reflection, and
only what is missing is done. With nothing missing it emits no DDL.

THE LITERALS BELOW ARE FROZEN COPIES of backend/enums.py.EventType as it stood
when this was written, in its declaration order -- the order models._one_of
emits, which tests/test_group_schema.py's schema comparison depends on.

Revision ID: b7e3d91c4f2a
Revises: a8c2e5f19b47
Create Date: 2026-10-16

"""
import sqlalchemy as sa
from sqlalchemy import inspect, text

from alembic import op

# revision identifiers, used by Alembic.
revision = 'b7e3d91c4f2a'
down_revision = 'a8c2e5f19b47'
branch_labels = None
depends_on = None


TABLE = 'transaction_logs'

EVENT_TYPE = (
    'HANDOVER', 'HANDOVER_LOC', 'VERIFICATION', 'FIX', 'ASSIGN',
    'FAULT', 'RECLASSIFY', 'CREATE', 'CONDITION_REPORT',
)
CHECK_NAME = 'ck_transaction_logs_event_type'

# old spelling -> EventType value. See the module docstring.
LEGACY_SPELLINGS = {'FAULT_REPORT': 'FAULT', 'REPAIR': 'FIX'}

# (column, referenced table). Each references `id`, RESTRICT, named
# fk_transaction_logs_<column>, indexed as ix_transaction_logs_<column>.
ID_COLUMNS = (
    ('from_holder_user_id', 'users'),
    ('to_holder_user_id', 'users'),
    ('from_owner_user_id', 'users'),
    ('to_owner_user_id', 'users'),
    ('from_group_id', 'groups'),
    ('to_group_id', 'groups'),
)
TEXT_COLUMNS = ('from_location', 'to_location', 'reason')

DEAD_COLUMN = 'involved_location_id'
DEAD_REFERRED = 'locations'

# a8c2e5f19b47's convention, for the same reason: on SQLite batch mode reflects
# the table and needs to know what the constraints it rebuilds are called.
NAMING_CONVENTION = {'fk': 'fk_%(table_name)s_%(column_0_name)s'}


# index -> the index of a later revision that replaced it. See upgrade().
SUPERSEDED = {
    'ix_transaction_logs_from_group_id': 'ix_transaction_logs_from_group_id_timestamp',
}


def _fk_name(column):
    return f'fk_{TABLE}_{column}'


def _ix_name(column):
    return f'ix_{TABLE}_{column}'


def _sql_list(values):
    """The values as a SQL literal list. DDL cannot take bind parameters."""
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


def _state(inspector):
    return {
        'columns': {c['name']: c for c in inspector.get_columns(TABLE)},
        'fks': {fk['name'] for fk in inspector.get_foreign_keys(TABLE)},
        'indexes': {ix['name'] for ix in inspector.get_indexes(TABLE)},
        'checks': {c.get('name') for c in inspector.get_check_constraints(TABLE)},
    }


def _refuse_bad_event_types(conn):
    """Raise, naming every row, if a value is neither a member nor renamed."""
    accepted = EVENT_TYPE + tuple(LEGACY_SPELLINGS)
    rows = conn.execute(text(
        f"SELECT id, event_type FROM {TABLE} "
        f"WHERE event_type IS NULL OR event_type NOT IN ({_sql_list(accepted)}) "
        "ORDER BY id"
    )).all()
    if rows:
        raise RuntimeError(
            f"Refusing to migrate: {len(rows)} transaction_logs row(s) hold an "
            "event_type that is NULL or outside the vocabulary the column is "
            "about to be constrained to:\n  "
            + "\n  ".join(f"transaction_logs #{row[0]} holds {row[1]!r}" for row in rows)
            + "\n\nCorrect each value to one of "
            + ", ".join(EVENT_TYPE)
            + " (backend/enums.py EventType), then re-run. Nothing has been changed."
        )


def upgrade():
    conn = op.get_bind()
    state = _state(inspect(conn))
    columns = state['columns']

    # EVERY check before ANY change, renames included. The transaction would
    # roll a rename back on a refusal anyway, on both dialects; checking first
    # just keeps the order plain. The check runs even when the CHECK and NOT
    # NULL are already in place: it is one SELECT, and there it finds nothing.
    _refuse_bad_event_types(conn)

    for old, new in LEGACY_SPELLINGS.items():
        conn.execute(
            text(f"UPDATE {TABLE} SET event_type = :new WHERE event_type = :old"),
            {"new": new, "old": old},
        )

    add_columns = [
        sa.Column(name, sa.Integer(), nullable=True)
        for name, _referred in ID_COLUMNS if name not in columns
    ] + [
        sa.Column(name, sa.String(), nullable=True)
        for name in TEXT_COLUMNS if name not in columns
    ]
    add_fks = [(name, referred) for name, referred in ID_COLUMNS if _fk_name(name) not in state['fks']]
    # Less an index a LATER revision replaced, once its replacement exists:
    # 3c6a0f8e2d71 drops ix_transaction_logs_from_group_id for a composite
    # that leads with the same column. The composite exists only on a
    # database built after that revision -- the create_all databases this
    # re-runs against -- and there the index stays gone.
    add_indexes = [
        name for name, _referred in ID_COLUMNS
        if _ix_name(name) not in state['indexes']
        and SUPERSEDED.get(_ix_name(name)) not in state['indexes']
    ]
    add_check = CHECK_NAME not in state['checks']
    set_not_null = columns['event_type']['nullable']
    drop_dead = DEAD_COLUMN in columns

    # No early return when everything is present: every list above is then
    # empty and every flag false, and batch mode given no operations emits
    # nothing on either dialect. Measured, by a mutant that added one and
    # changed no test's result.
    with op.batch_alter_table(TABLE, naming_convention=NAMING_CONVENTION) as batch_op:
        for column in add_columns:
            batch_op.add_column(column)
        for name, referred in add_fks:
            batch_op.create_foreign_key(_fk_name(name), referred, [name], ['id'], ondelete='RESTRICT')
        for name in add_indexes:
            batch_op.create_index(_ix_name(name), [name], unique=False)
        if set_not_null:
            batch_op.alter_column('event_type', existing_type=sa.String(), nullable=False)
        if add_check:
            batch_op.create_check_constraint(CHECK_NAME, f"event_type IN ({_sql_list(EVENT_TYPE)})")
        if drop_dead:
            if _ix_name(DEAD_COLUMN) in state['indexes']:
                batch_op.drop_index(_ix_name(DEAD_COLUMN))
            if _fk_name(DEAD_COLUMN) in state['fks']:
                batch_op.drop_constraint(_fk_name(DEAD_COLUMN), type_='foreignkey')
            batch_op.drop_column(DEAD_COLUMN)


def downgrade():
    """Everything above, reversed. The new columns' values are lost.

    involved_location_id comes back nullable, empty, with its RESTRICT foreign
    key and its index -- the shape a8c2e5f19b47 left it in.
    """
    conn = op.get_bind()
    state = _state(inspect(conn))
    columns = state['columns']

    drop_names = [name for name, _referred in ID_COLUMNS] + list(TEXT_COLUMNS)
    drop_columns = [name for name in drop_names if name in columns]
    drop_fks = [name for name, _referred in ID_COLUMNS if _fk_name(name) in state['fks']]
    drop_indexes = [name for name, _referred in ID_COLUMNS if _ix_name(name) in state['indexes']]
    drop_check = CHECK_NAME in state['checks']
    set_nullable = not columns['event_type']['nullable']
    restore_dead = DEAD_COLUMN not in columns

    with op.batch_alter_table(TABLE, naming_convention=NAMING_CONVENTION) as batch_op:
        if restore_dead:
            batch_op.add_column(sa.Column(DEAD_COLUMN, sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                _fk_name(DEAD_COLUMN), DEAD_REFERRED, [DEAD_COLUMN], ['id'], ondelete='RESTRICT'
            )
            batch_op.create_index(_ix_name(DEAD_COLUMN), [DEAD_COLUMN], unique=False)
        if drop_check:
            batch_op.drop_constraint(CHECK_NAME, type_='check')
        if set_nullable:
            batch_op.alter_column('event_type', existing_type=sa.String(), nullable=True)
        for name in drop_indexes:
            batch_op.drop_index(_ix_name(name))
        for name in drop_fks:
            batch_op.drop_constraint(_fk_name(name), type_='foreignkey')
        for name in drop_columns:
            batch_op.drop_column(name)
