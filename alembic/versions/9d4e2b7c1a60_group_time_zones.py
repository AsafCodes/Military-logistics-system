"""give every group a time zone

DATA-M9-1. The daily movement report is to cover a group's calendar day, and
a day has edges only in a zone. This adds groups.timezone, an IANA zone name,
NOT NULL.

Every group that exists when this runs gets 'Asia/Jerusalem', the zone this
project has assumed (backend.clock.DEFAULT_TIMEZONE). The column is
added with that as its server default, which fills the rows that are there,
and the default is then dropped: the model has none, so every writer has to
name a zone, and the database agrees with the model on that.

No CHECK on the value. The zone list changes with tzdata releases, and a list
frozen here would refuse a zone IANA adds later. Names are checked on the way
in, by backend.clock.zone.

THIS REVISION IS IDEMPOTENT, for the reason every schema revision after
backend.migrations.BASELINE_STAMP is: it also runs against create_all
databases that already have the column. The column is checked by name, and
with it present nothing is done and no DDL is emitted.

THE LITERAL BELOW IS A FROZEN COPY of backend.clock.DEFAULT_TIMEZONE as it
stood when this was written. A later change to the default must not change
what this revision did.

Revision ID: 9d4e2b7c1a60
Revises: b7e3d91c4f2a
Create Date: 2026-10-17

"""
import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

# revision identifiers, used by Alembic.
revision = '9d4e2b7c1a60'
down_revision = 'b7e3d91c4f2a'
branch_labels = None
depends_on = None


TABLE = 'groups'
COLUMN = 'timezone'
BACKFILL = 'Asia/Jerusalem'


def _has_column(conn):
    return COLUMN in {c['name'] for c in inspect(conn).get_columns(TABLE)}


def upgrade():
    if _has_column(op.get_bind()):
        return

    # Added NOT NULL with a server default, which is what fills the existing
    # rows on both dialects; then the default goes. On SQLite the second step
    # rebuilds the table (batch mode), on Postgres it is one ALTER.
    with op.batch_alter_table(TABLE) as batch_op:
        batch_op.add_column(
            sa.Column(COLUMN, sa.String(), nullable=False, server_default=BACKFILL)
        )
    with op.batch_alter_table(TABLE) as batch_op:
        batch_op.alter_column(COLUMN, existing_type=sa.String(), server_default=None)


def downgrade():
    if not _has_column(op.get_bind()):
        return
    with op.batch_alter_table(TABLE) as batch_op:
        batch_op.drop_column(COLUMN)
