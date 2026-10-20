"""keep every take of a maintenance ticket, released ones included

DATA-M22-2. DATA-M22-1 records the CURRENT claim on the ticket
(maintenance_logs.assigned_to_id and started_at), and a release clears both,
so nothing kept who had held a ticket. This revision adds ticket_claims, one
row per take:

- ticket_id, user_id (who took it) and started_at, all NOT NULL;
- ended_at, ended_by_id (who released or closed it) and outcome
  ('Released' or 'Closed'), NULL while the claim is open, and empty or filled
  together (ck_ticket_claims_ended);
- every key RESTRICT and named fk_ticket_claims_<column>, every key column
  indexed, as every key has been since DATA-H13;
- uq_ticket_claims_open_ticket, a partial unique index on ticket_id WHERE
  ended_at IS NULL: at most one open claim per ticket.

BACKFILL, from what the ticket still says:

- an In Progress ticket with an assignee gets one open claim, taken by the
  assignee at started_at;
- a Closed ticket with an assignee gets one claim ended 'Closed' at closed_at,
  by technician_id, the closer (DATA-M21).

Releases made before this revision left nothing behind and are not recovered.
started_at falls back to opened_at, closed_at to the claim's start and
technician_id to the assignee, for rows edited by hand: the routes write all
three. A closed_at before the claim's start is also replaced by the start, so
no claim ends before it begins. A ticket with neither started_at nor
opened_at gets no claim, since a claim cannot begin at no time.

Both INSERTs skip a ticket that already has a claim, so a second run inserts
nothing, and the backfill runs whether or not this revision created the table:
a create_all database built from the M22-2 models has the table, and may have
In Progress tickets with no claim if nothing ever wrote one.

THIS REVISION IS IDEMPOTENT, for the reason every schema revision after
backend.migrations.BASELINE_STAMP is: it also runs against create_all
databases that already have all of it. The table and each index are checked
by name, and only what is missing is done. With nothing to do it emits no DDL.
It is also the first revision after the stamp to add a whole table, which is
why backend.migrations.TABLES_ADDED_AFTER_BASELINE exists.

THE NAMES AND VALUES BELOW ARE FROZEN, like every literal in this chain.

Revision ID: 5e8b1d4f7c29
Revises: 7a2f5c9d1e38
Create Date: 2026-10-19

"""
import sqlalchemy as sa
from sqlalchemy import inspect, text

from alembic import op

# revision identifiers, used by Alembic.
revision = '5e8b1d4f7c29'
down_revision = '7a2f5c9d1e38'
branch_labels = None
depends_on = None


TABLE = 'ticket_claims'
OPEN_INDEX = 'uq_ticket_claims_open_ticket'
OPEN = 'ended_at IS NULL'

# column -> the table its key refers to.
KEYS = {
    'ticket_id': 'maintenance_logs',
    'user_id': 'users',
    'ended_by_id': 'users',
}

# The plain indexes: the primary key's, as `index=True` on id names it, and
# one per key column.
INDEXES = {f'ix_{TABLE}_{column}': column for column in ('id', *KEYS)}

START = 'COALESCE(m.started_at, m.opened_at)'
# closed_at, unless it is missing or earlier than START. A NULL closed_at
# fails the comparison, so one CASE covers both.
END = f'CASE WHEN m.closed_at >= {START} THEN m.closed_at ELSE {START} END'


def _backfill(conn):
    conn.execute(text(
        f"INSERT INTO {TABLE} (ticket_id, user_id, started_at) "
        f"SELECT m.id, m.assigned_to_id, {START} "
        "FROM maintenance_logs m "
        "WHERE m.status = 'In Progress' AND m.assigned_to_id IS NOT NULL "
        f"AND {START} IS NOT NULL "
        f"AND NOT EXISTS (SELECT 1 FROM {TABLE} c WHERE c.ticket_id = m.id)"
    ))
    conn.execute(text(
        f"INSERT INTO {TABLE} "
        "(ticket_id, user_id, started_at, ended_at, ended_by_id, outcome) "
        f"SELECT m.id, m.assigned_to_id, {START}, {END}, "
        "COALESCE(m.technician_id, m.assigned_to_id), 'Closed' "
        "FROM maintenance_logs m "
        "WHERE m.status = 'Closed' AND m.assigned_to_id IS NOT NULL "
        f"AND {START} IS NOT NULL "
        f"AND NOT EXISTS (SELECT 1 FROM {TABLE} c WHERE c.ticket_id = m.id)"
    ))


def upgrade():
    conn = op.get_bind()
    inspector = inspect(conn)

    if TABLE not in inspector.get_table_names():
        op.create_table(
            TABLE,
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('ticket_id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('ended_by_id', sa.Integer(), nullable=True),
            sa.Column('outcome', sa.String(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
            *(
                sa.ForeignKeyConstraint(
                    [column], [f'{referred}.id'],
                    name=f'fk_{TABLE}_{column}', ondelete='RESTRICT',
                )
                for column, referred in KEYS.items()
            ),
            sa.CheckConstraint(
                "outcome IN ('Released', 'Closed')", name='ck_ticket_claims_outcome'
            ),
            sa.CheckConstraint(
                "(ended_at IS NULL AND ended_by_id IS NULL AND outcome IS NULL)"
                " OR (ended_at IS NOT NULL AND ended_by_id IS NOT NULL AND outcome IS NOT NULL)",
                name='ck_ticket_claims_ended',
            ),
        )

    present = {ix['name'] for ix in inspect(conn).get_indexes(TABLE)}
    for name, column in INDEXES.items():
        if name not in present:
            op.create_index(name, TABLE, [column], unique=False)
    if OPEN_INDEX not in present:
        op.create_index(
            OPEN_INDEX, TABLE, ['ticket_id'], unique=True,
            sqlite_where=text(OPEN), postgresql_where=text(OPEN),
        )

    _backfill(conn)


def downgrade():
    """Drops the table, and with it every take it recorded."""
    if TABLE in inspect(op.get_bind()).get_table_names():
        op.drop_table(TABLE)
