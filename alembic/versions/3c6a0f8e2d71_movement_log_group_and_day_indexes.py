"""file every movement under a group, and index a group's day

DATA-M9-2. The daily movement report becomes one group's calendar day. A row
belongs to that day through the row itself, not through wherever the item is
now, so a day that has passed keeps its rows when the item moves on. This
revision:

- adds transaction_logs.group_id, the item's group when the event happened:
  nullable, a RESTRICT foreign key to groups, named fk_transaction_logs_group_id;
- backfills it, once, when it adds it (below);
- adds the two composite indexes the report's two arms range over,
  (group_id, timestamp) and (from_group_id, timestamp);
- drops ix_transaction_logs_from_group_id, whose one column is now the
  prefix of the second composite, and ix_transaction_logs_timestamp, whose
  one reader was the old 24-hour report.

THE BACKFILL IS EXACT FOR A CUSTODY ROW AND AN APPROXIMATION FOR THE REST.
A row with a to_group_id is a custody row from DATA-M8-1 on, and its group is
that column: the group the event left the item in, which is what
audit_trail.record_event writes from now on. Every other row with an item --
a fault, a fix, a verification, and a custody row from before DATA-M8-1 --
gets the item's group as it stands while this runs. Nothing recorded where
the item was at the time, so that is a guess, made once, and from then on the
row keeps it whatever the item does next. A row with no item stays NULL; the
report has never shown those (it joins the item).

Only when the column is ADDED. On a database that already has it, every row
was written by code that writes it, and there is nothing to guess.

THIS REVISION IS IDEMPOTENT, for the reason every schema revision after
backend.migrations.BASELINE_STAMP is: it also runs against create_all
databases that already have all of it. The column, the foreign key and each
index are checked by name, and only what is missing is done (or, for the two
dropped indexes, what is present). With nothing to do it emits no DDL.

f4d81b2c6a93 and b7e3d91c4f2a created the two indexes dropped here. Each
skips its index when the composite that replaced it is already present, which
is true only of a database built after this revision -- create_all from
today's models -- so a re-run of either does not bring the index back.

THE NAMES BELOW ARE FROZEN, like every literal in this chain.

Revision ID: 3c6a0f8e2d71
Revises: 9d4e2b7c1a60
Create Date: 2026-10-17

"""
import sqlalchemy as sa
from sqlalchemy import inspect, text

from alembic import op

# revision identifiers, used by Alembic.
revision = '3c6a0f8e2d71'
down_revision = '9d4e2b7c1a60'
branch_labels = None
depends_on = None


TABLE = 'transaction_logs'
COLUMN = 'group_id'
FK_NAME = 'fk_transaction_logs_group_id'

# name -> columns
NEW_INDEXES = {
    'ix_transaction_logs_group_id_timestamp': ('group_id', 'timestamp'),
    'ix_transaction_logs_from_group_id_timestamp': ('from_group_id', 'timestamp'),
}
# name -> column. Restored by the downgrade.
OLD_INDEXES = {
    'ix_transaction_logs_from_group_id': 'from_group_id',
    'ix_transaction_logs_timestamp': 'timestamp',
}

# a8c2e5f19b47's convention, for the same reason: on SQLite batch mode reflects
# the table and needs to know what the constraints it rebuilds are called.
NAMING_CONVENTION = {'fk': 'fk_%(table_name)s_%(column_0_name)s'}


def _state(inspector):
    return {
        'columns': {c['name'] for c in inspector.get_columns(TABLE)},
        'fks': {fk['name'] for fk in inspector.get_foreign_keys(TABLE)},
        'indexes': {ix['name'] for ix in inspector.get_indexes(TABLE)},
    }


def _backfill(conn):
    conn.execute(text(
        f"UPDATE {TABLE} SET group_id = to_group_id WHERE to_group_id IS NOT NULL"
    ))
    conn.execute(text(
        f"UPDATE {TABLE} SET group_id = "
        f"(SELECT equipment.group_id FROM equipment WHERE equipment.id = {TABLE}.equipment_id) "
        "WHERE group_id IS NULL AND equipment_id IS NOT NULL"
    ))


def upgrade():
    conn = op.get_bind()
    state = _state(inspect(conn))

    add_column = COLUMN not in state['columns']
    add_fk = FK_NAME not in state['fks']
    if add_column or add_fk:
        with op.batch_alter_table(TABLE, naming_convention=NAMING_CONVENTION) as batch_op:
            if add_column:
                batch_op.add_column(sa.Column(COLUMN, sa.Integer(), nullable=True))
            if add_fk:
                batch_op.create_foreign_key(FK_NAME, 'groups', [COLUMN], ['id'], ondelete='RESTRICT')
    if add_column:
        _backfill(conn)

    # Plain CREATE and DROP INDEX: neither rebuilds the table on either dialect.
    for name, columns in NEW_INDEXES.items():
        if name not in state['indexes']:
            op.create_index(name, TABLE, list(columns), unique=False)
    for name in OLD_INDEXES:
        if name in state['indexes']:
            op.drop_index(name, table_name=TABLE)


def downgrade():
    """Everything above, reversed. group_id's values are lost."""
    conn = op.get_bind()
    state = _state(inspect(conn))

    for name, column in OLD_INDEXES.items():
        if name not in state['indexes']:
            op.create_index(name, TABLE, [column], unique=False)
    for name in NEW_INDEXES:
        if name in state['indexes']:
            op.drop_index(name, table_name=TABLE)

    drop_fk = FK_NAME in state['fks']
    drop_column = COLUMN in state['columns']
    if drop_fk or drop_column:
        with op.batch_alter_table(TABLE, naming_convention=NAMING_CONVENTION) as batch_op:
            if drop_fk:
                batch_op.drop_constraint(FK_NAME, type_='foreignkey')
            if drop_column:
                batch_op.drop_column(COLUMN)
