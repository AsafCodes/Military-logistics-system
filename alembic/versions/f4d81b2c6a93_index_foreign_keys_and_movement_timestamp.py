"""index every foreign key, and the movement log's timestamp

DATA-H13-1. None of the seventeen foreign keys on the six legacy tables had an
index on the referencing side. Neither dialect builds one on its own -- a
foreign key indexes the column it POINTS AT, through that column's primary key,
and nothing on the column that does the pointing. So every equipment-derived
join (tickets, movements, verifications, history), the holder arm of the scope
predicate, and DATA-H7's "is this fault type still in use" count were each a
scan of a table that only grows.

NOT EVERY ONE OF THESE HAS A QUERY READING IT TODAY, and that is known rather
than overlooked. holder_user_id, fault_type_id and the four equipment_id
columns serve the reads named above. The rest -- the owner, location, catalog,
actor and verification references -- are only ever followed FROM the child row
TO its parent, which goes through the parent's primary key and uses no index
on the child. Nothing filters on them or looks a child up through them. They
are indexed for the other direction: deleting a parent row makes the database
look for children on each column that references it, and without an index
every such check is a scan of the child table. No route deletes a user or an
item yet. The second half of DATA-H13 is to declare what happens when one
does, and a rule about deletion is only cheap to enforce if these exist.

One column that is not a foreign key joins them: transaction_logs.timestamp.
The daily movement report asks for the last 24 hours of a log that keeps every
movement ever made, and that range is the selective half of its query. Measured
on SQLite at 200,000 log rows over two years, with statistics gathered: 0.6 ms
through this index against 53 ms without it, where the best remaining plan
walks every visible item's whole history and sorts what survives.

DELIBERATELY NOT INDEXED, though the ticket names them:

  - maintenance_logs.opened_at. The ticket list sorts by it and has no LIMIT,
    so every visible ticket is fetched and sorted whatever index exists.
    Measured at 50,000 tickets, the planner never chose an index on it and the
    timing did not move. It becomes worth having the day that route paginates,
    which is DATA-M10's to do.
  - equipment.status and maintenance_logs.status. Four values each, so an
    index on either is rarely selective enough for a planner to choose.
  - verifications.created_date and equipment_status_history.created_date. Only
    ever sorted within one item's rows, which the equipment_id index below has
    already narrowed.

THIS REVISION IS IDEMPOTENT, for the reason d3a9c17be540 is: it lands after
backend.migrations.BASELINE_STAMP, so it also runs against a create_all
database that already carries every one of these indexes. It checks for each
by name and creates only the missing ones. Unlike a duplicate check constraint,
a duplicate index is loud on BOTH dialects -- which makes the skip necessary
for startup to succeed at all rather than for a failure to be noticed.

A NAME IS NOT TAKEN ON TRUST. An index that already carries one of these names
is skipped only if it is the index this revision would have built: on that
table, on that one column, not unique, and not partial. Any other INDEX under
the name -- on any table, since neither dialect scopes an index name to its
table -- is refused before any change, naming it. Skipping it would record this
revision as applied over an index on the wrong column, or one that covers only
some rows, or a UNIQUE one -- which is not a slower index but a rule, and on
holder_user_id would forbid one soldier holding two items.

Only columns, uniqueness and a WHERE clause are compared. An index under the
right name with some other peculiarity is accepted as it stands, and a
Postgres TABLE or SEQUENCE squatting on one of these names is not looked for at
all: that meets CREATE INDEX directly and fails with the server's own message.

That check binds later revisions too, and it is better met here than at a
deployment. This revision re-runs against whatever create_all builds from the
models of the day, so a future change that keeps one of these names and alters
what it indexes -- making it unique, say -- turns this into a refusal for every
create_all database. Give such an index a new name.
tests/test_group_schema.py::test_a_create_all_database_from_todays_models_is_already_at_head
is the test that goes red.

Plain CREATE INDEX, not batch mode. Adding an index rebuilds no table on either
dialect, so nothing here copies a row.

THE NAMES BELOW ARE FROZEN, like every literal in this chain. They are what
`index=True` generates today (ix_<table>_<column>), written out rather than
derived, so that a later change to the models cannot rewrite what this revision
did. tests/test_group_schema.py::test_migration_and_create_all_build_the_same_schema
is what notices if the two drift.

Revision ID: f4d81b2c6a93
Revises: d3a9c17be540
Create Date: 2026-10-02

"""
from sqlalchemy import inspect

from alembic import op

# revision identifiers, used by Alembic.
revision = 'f4d81b2c6a93'
down_revision = 'd3a9c17be540'
branch_labels = None
depends_on = None


# (table, column). The index is named ix_<table>_<column>.
INDEXES = (
    ('equipment', 'catalog_item_id'),
    ('equipment', 'owner_user_id'),
    ('equipment', 'owner_location_id'),
    ('equipment', 'holder_user_id'),
    ('equipment', 'actual_location_id'),
    ('transaction_logs', 'equipment_id'),
    ('transaction_logs', 'involved_user_id'),
    ('transaction_logs', 'involved_location_id'),
    ('transaction_logs', 'timestamp'),
    ('fault_types', 'requested_by_id'),
    ('maintenance_logs', 'equipment_id'),
    ('maintenance_logs', 'fault_type_id'),
    ('maintenance_logs', 'technician_id'),
    ('verifications', 'equipment_id'),
    ('verifications', 'created_by'),
    ('equipment_status_history', 'equipment_id'),
    ('equipment_status_history', 'verification_id'),
    ('equipment_status_history', 'created_by'),
)


def _index_name(table, column):
    return f'ix_{table}_{column}'


def _existing_indexes(inspector, table):
    return {index['name']: index for index in inspector.get_indexes(table)}


def _every_index(inspector):
    """name -> (table, reflected index), across the whole database."""
    return {
        name: (table, index)
        for table in inspector.get_table_names()
        for name, index in _existing_indexes(inspector, table).items()
    }


def _is_partial(index):
    """Whether the index carries a WHERE clause, on either dialect.

    Reflected as sqlite_where or postgresql_where under dialect_options, and
    absent altogether for an ordinary index.
    """
    options = index.get('dialect_options') or {}
    return any(key.endswith('_where') and value is not None for key, value in options.items())


def upgrade():
    inspector = inspect(op.get_bind())
    present = _every_index(inspector)

    # EVERY check before ANY change, and every conflict in one message, as
    # d3a9c17be540 does for its rows and for the same reason.
    missing = []
    conflicts = []
    for table, column in INDEXES:
        name = _index_name(table, column)
        if name not in present:
            missing.append((name, table, column))
            continue

        on_table, existing = present[name]
        if (
            on_table != table
            or existing['column_names'] != [column]
            or existing['unique']
            or _is_partial(existing)
        ):
            shape = ('UNIQUE ' if existing['unique'] else '') + ('partial ' if _is_partial(existing) else '')
            conflicts.append(
                f"{name}: found a {shape or 'plain '}index on {on_table} "
                f"{tuple(existing['column_names'])}, expected a plain one on {table} ({column!r},)"
            )

    if conflicts:
        raise RuntimeError(
            "Refusing to migrate: an index already carries a name this revision "
            "is about to use, and it is not the index this revision builds:\n  "
            + "\n  ".join(conflicts)
            + "\n\nRename or remove each one, then re-run. Nothing has been changed."
        )

    for name, table, column in missing:
        op.create_index(name, table, [column], unique=False)


def downgrade():
    inspector = inspect(op.get_bind())

    for table, column in reversed(INDEXES):
        name = _index_name(table, column)
        if name not in _existing_indexes(inspector, table):
            continue
        op.drop_index(name, table_name=table)
