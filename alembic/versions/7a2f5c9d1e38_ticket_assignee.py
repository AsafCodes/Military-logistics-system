"""record who took a maintenance ticket, and when

DATA-M22. A ticket can now be taken (Open -> In Progress) and released again.
This revision adds the two columns that record it:

- maintenance_logs.assigned_to_id: who took it. Nullable, a RESTRICT foreign
  key to users named fk_maintenance_logs_assigned_to_id, and indexed
  (ix_maintenance_logs_assigned_to_id), like every foreign key since DATA-H13-1;
- maintenance_logs.started_at: when, a TIMESTAMPTZ on Postgres like every
  clock.UtcDateTime column since e5f1b8d24a07.

No backfill. Nothing took a ticket before this, so NULL is the true value of
both on every row there is.

THIS REVISION IS IDEMPOTENT, for the reason every schema revision after
backend.migrations.BASELINE_STAMP is: it also runs against create_all
databases that already have all of it. Each column, the foreign key and the
index are checked by name, and only what is missing is done. With nothing to
do it emits no DDL.

THE NAMES BELOW ARE FROZEN, like every literal in this chain.

Revision ID: 7a2f5c9d1e38
Revises: 3c6a0f8e2d71
Create Date: 2026-10-19

"""
import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

# revision identifiers, used by Alembic.
revision = '7a2f5c9d1e38'
down_revision = '3c6a0f8e2d71'
branch_labels = None
depends_on = None


TABLE = 'maintenance_logs'
COLUMN = 'assigned_to_id'
STARTED = 'started_at'
FK_NAME = 'fk_maintenance_logs_assigned_to_id'
INDEX_NAME = 'ix_maintenance_logs_assigned_to_id'

# a8c2e5f19b47's convention, for the same reason: on SQLite batch mode reflects
# the table and needs to know what the constraints it rebuilds are called.
NAMING_CONVENTION = {'fk': 'fk_%(table_name)s_%(column_0_name)s'}


def _state(inspector):
    return {
        'columns': {c['name'] for c in inspector.get_columns(TABLE)},
        'fks': {fk['name'] for fk in inspector.get_foreign_keys(TABLE)},
        'indexes': {ix['name'] for ix in inspector.get_indexes(TABLE)},
    }


def upgrade():
    state = _state(inspect(op.get_bind()))

    add_column = COLUMN not in state['columns']
    add_started = STARTED not in state['columns']
    add_fk = FK_NAME not in state['fks']
    if add_column or add_started or add_fk:
        with op.batch_alter_table(TABLE, naming_convention=NAMING_CONVENTION) as batch_op:
            if add_column:
                batch_op.add_column(sa.Column(COLUMN, sa.Integer(), nullable=True))
            if add_started:
                batch_op.add_column(
                    sa.Column(STARTED, sa.DateTime(timezone=True), nullable=True)
                )
            if add_fk:
                batch_op.create_foreign_key(FK_NAME, 'users', [COLUMN], ['id'], ondelete='RESTRICT')

    # A plain CREATE INDEX: it rebuilds the table on neither dialect.
    if INDEX_NAME not in state['indexes']:
        op.create_index(INDEX_NAME, TABLE, [COLUMN], unique=False)


def downgrade():
    """Everything above, reversed. Who took which ticket is lost."""
    state = _state(inspect(op.get_bind()))

    if INDEX_NAME in state['indexes']:
        op.drop_index(INDEX_NAME, table_name=TABLE)

    drop_fk = FK_NAME in state['fks']
    drop_column = COLUMN in state['columns']
    drop_started = STARTED in state['columns']
    if drop_fk or drop_column or drop_started:
        with op.batch_alter_table(TABLE, naming_convention=NAMING_CONVENTION) as batch_op:
            if drop_fk:
                batch_op.drop_constraint(FK_NAME, type_='foreignkey')
            if drop_column:
                batch_op.drop_column(COLUMN)
            if drop_started:
                batch_op.drop_column(STARTED)
