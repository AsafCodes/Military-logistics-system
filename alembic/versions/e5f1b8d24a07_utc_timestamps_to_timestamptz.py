"""utc timestamps to timestamptz

DATA-H1-2. Moves every clock.UtcDateTime column from TIMESTAMP WITHOUT TIME
ZONE to TIMESTAMPTZ **on Postgres only**, so the database describes these
values as the UTC instants they have always been. Nothing a user can see
changes: DATA-H1-1 already made Python hand out aware UTC on both backends.
What changes is that psql, a report, or any future non-ORM reader stops seeing
a bare wall-clock string with nothing saying which zone it is in.

SQLite is a deliberate no-op -- see upgrade().

WHY THE `USING` CLAUSE IS THE WHOLE MIGRATION
---------------------------------------------
`ALTER COLUMN ... TYPE TIMESTAMPTZ` without an explicit USING applies an
implicit cast, and that cast reads the existing naive values in the SERVER's
`TimeZone` setting. On any server not set to UTC -- which nothing in this
repository guarantees -- that silently shifts every timestamp in the database
by the server's offset. That is DATA-H1's own defect reintroduced one layer
down, by the migration that claims to close it.

`col AT TIME ZONE 'UTC'` states the interpretation instead of inheriting it,
and downgrade() needs the identical clause in the other direction: applied to
a timestamptz, `AT TIME ZONE 'UTC'` yields the UTC wall time, which is exactly
the naive value the column held before. Pinned by
tests/test_utc_migration_postgres.py, which runs this migration under a
deliberately non-UTC session TimeZone -- the only arrangement in which a
dropped USING clause is visible.

Note that ALTER COLUMN TYPE takes an ACCESS EXCLUSIVE lock and rewrites each
table. Irrelevant at this data size; not irrelevant at every data size.

Revision ID: e5f1b8d24a07
Revises: a7226c349ecf
Create Date: 2026-08-29 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e5f1b8d24a07'
down_revision: Union[str, Sequence[str], None] = 'a7226c349ecf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Every clock.UtcDateTime column in backend/models.py. daily_stats.date is on
# the dead analytics table (DATA-M18) and converts anyway, so the column type
# stays uniform and this list has no exceptions to explain.
COLUMNS = (
    ('users', 'last_seen'),
    ('equipment', 'last_verified_at'),
    ('transaction_logs', 'timestamp'),
    ('maintenance_logs', 'opened_at'),
    ('maintenance_logs', 'closed_at'),
    ('daily_stats', 'date'),
    ('verifications', 'created_date'),
    ('equipment_status_history', 'created_date'),
)


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == 'postgresql'


def upgrade() -> None:
    """Upgrade schema.

    Returns immediately on SQLite, and not as a shortcut: clock.UtcDateTime's
    load_dialect_impl hands SQLite a plain DateTime, so there is no type change
    to make there and the migrated schema already equals what create_all builds
    (pinned by test_group_schema.test_migration_and_create_all_build_the_same_
    schema). A no-op batch_alter_table would not be free either -- batch mode
    rebuilds a table by drop-and-rename, which is the exact operation
    backend/migrations.py builds a separate foreign-key-free engine to survive.
    """
    if not _is_postgres():
        return

    for table, column in COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=sa.DateTime(),
            type_=sa.DateTime(timezone=True),
            postgresql_using=f"{column} AT TIME ZONE 'UTC'",
        )


def downgrade() -> None:
    """Downgrade schema -- genuinely reversible, unlike this chain's others.

    c93f2a615d84 and a7226c349ecf both note that their downgrades restore a
    shape without restoring data. This one restores both: the instants are
    preserved in either direction, because `AT TIME ZONE 'UTC'` is its own
    inverse across these two types. Dropping the clause here would corrupt on
    the way back exactly as it would on the way out.
    """
    if not _is_postgres():
        return

    for table, column in COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=sa.DateTime(timezone=True),
            type_=sa.DateTime(),
            postgresql_using=f"{column} AT TIME ZONE 'UTC'",
        )
