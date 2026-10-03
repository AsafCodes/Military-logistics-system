"""declare a deletion rule on every foreign key

DATA-H13-2. None of the seventeen foreign keys on the six legacy tables said
what happens to a row when the row it references is deleted, and neither did
equipment.group_id. All eighteen now say ON DELETE RESTRICT.

RESTRICT, and nothing softer, because of what these tables are. A transaction
log, a ticket, a verification and a status history row are each a record that
something happened to an item, by someone. CASCADE would let one DELETE of an
item erase its whole history; SET NULL would keep the history and forget who
or what it was about. Users are retired through is_active_duty rather than
deleted, and anything else that must go has to have its references cleared
deliberately first.

THIS CHANGES NO BEHAVIOUR, and it is worth being plain about that. With no rule
at all a foreign key already refuses the delete of a referenced row: the
default is NO ACTION, which differs from RESTRICT only in when the check is
made. What changes is that the refusal is now a decision written in the schema
rather than the absence of one -- so "should deleting a user cascade?" has an
answer to read and to change, instead of a default to rediscover.

THE SEVENTEEN ARE ALSO NAMED HERE, fk_<table>_<column>, and that is not
decoration. They were declared without names, so Postgres had named each one
<table>_<column>_fkey and SQLite had not named them at all. A constraint with
no name cannot be dropped by one, which is what replacing its rule requires,
and a model that names what a migration leaves anonymous makes create_all and
Alembic build different schemas. equipment.group_id already had its name.

HOW A RULE IS REPLACED. There is no ALTER for it on either dialect: the
constraint is dropped and created again. On Postgres that is two statements
per foreign key, and the ADD validates every existing row -- which cannot fail,
since the constraint being replaced already guaranteed the same thing. On
SQLite it is a rebuild of the whole table, once per table, through batch mode;
the naming convention handed to it below is what gives the unnamed constraints
a name to be dropped by.

DANGLING REFERENCES ARE NEITHER FOUND NOR REPAIRED. A SQLite database written
before backend.database began enforcing foreign keys can hold a row pointing at
nothing, and the rebuild copies it across as it stands: migrations run without
enforcement, because batch mode cannot run with it. Such a row was there
before this revision and is no worse after it. Finding them is its own piece
of work, registered beside this ticket rather than folded into it.

THIS REVISION IS IDEMPOTENT, for the reason the two before it are: it lands
after backend.migrations.BASELINE_STAMP, so it also runs against a create_all
database whose foreign keys are already named and ruled. A foreign key is left
alone when it already carries the name below AND declares a rule -- any rule.
Not "declares RESTRICT": this revision re-runs against whatever create_all
builds from the models of the day, and if one of these is ever deliberately
changed to CASCADE, a check for RESTRICT here would quietly change it back on
every database that takes the baseline path.

THE DOWNGRADE REMOVES THE RULE AND KEEPS THE NAME. Batch mode cannot create a
constraint without a name, so the seventeen cannot be returned to anonymity on
SQLite, and leaving the two dialects in different states would be worse than
leaving both slightly ahead of where they started. A downgraded database has
every foreign key it had, under the names given here, with no deletion rule.

THE NAMES AND TARGETS BELOW ARE FROZEN, like every literal in this chain.
tests/test_group_schema.py::test_migration_and_create_all_build_the_same_schema
is what notices if the models drift from them.

Revision ID: a8c2e5f19b47
Revises: f4d81b2c6a93
Create Date: 2026-10-03

"""
from sqlalchemy import inspect

from alembic import op

# revision identifiers, used by Alembic.
revision = 'a8c2e5f19b47'
down_revision = 'f4d81b2c6a93'
branch_labels = None
depends_on = None


RULE = 'RESTRICT'

# What batch mode calls a reflected foreign key that has no name of its own.
# It produces exactly the names below, which is the point: on SQLite the
# constraint being dropped and the one being created answer to the same name.
NAMING_CONVENTION = {'fk': 'fk_%(table_name)s_%(column_0_name)s'}

# (table, column, referenced table). Every one references `id`, and the
# constraint is named fk_<table>_<column>.
FOREIGN_KEYS = (
    ('equipment', 'catalog_item_id', 'catalog_items'),
    ('equipment', 'group_id', 'groups'),
    ('equipment', 'owner_user_id', 'users'),
    ('equipment', 'owner_location_id', 'locations'),
    ('equipment', 'holder_user_id', 'users'),
    ('equipment', 'actual_location_id', 'locations'),
    ('transaction_logs', 'equipment_id', 'equipment'),
    ('transaction_logs', 'involved_user_id', 'users'),
    ('transaction_logs', 'involved_location_id', 'locations'),
    ('fault_types', 'requested_by_id', 'users'),
    ('maintenance_logs', 'equipment_id', 'equipment'),
    ('maintenance_logs', 'fault_type_id', 'fault_types'),
    ('maintenance_logs', 'technician_id', 'users'),
    ('verifications', 'equipment_id', 'equipment'),
    ('verifications', 'created_by', 'users'),
    ('equipment_status_history', 'equipment_id', 'equipment'),
    ('equipment_status_history', 'verification_id', 'verifications'),
    ('equipment_status_history', 'created_by', 'users'),
)


def _constraint_name(table, column):
    return f'fk_{table}_{column}'


def _tables():
    """The tables above, each once, in the order they first appear."""
    return list(dict.fromkeys(table for table, _column, _referred in FOREIGN_KEYS))


def _reflected(inspector, table):
    """column -> the reflected foreign key on that single column."""
    return {
        fk['constrained_columns'][0]: fk
        for fk in inspector.get_foreign_keys(table)
        if len(fk['constrained_columns']) == 1
    }


def _rule(fk):
    return (fk.get('options') or {}).get('ondelete')


def _replace(table, pending, ondelete):
    """Drop and re-create each (column, referred, existing) under its name."""
    with op.batch_alter_table(table, naming_convention=NAMING_CONVENTION) as batch_op:
        for column, referred, existing in pending:
            name = _constraint_name(table, column)
            if existing is not None:
                # Postgres knows the old constraint as <table>_<column>_fkey and
                # must be given that; SQLite reflects no name, and batch mode
                # has already labelled it with the convention's.
                batch_op.drop_constraint(existing['name'] or name, type_='foreignkey')
            batch_op.create_foreign_key(name, referred, [column], ['id'], ondelete=ondelete)


def upgrade():
    inspector = inspect(op.get_bind())

    for table in _tables():
        reflected = _reflected(inspector, table)
        pending = []
        for fk_table, column, referred in FOREIGN_KEYS:
            if fk_table != table:
                continue
            existing = reflected.get(column)
            if (
                existing is not None
                and existing['name'] == _constraint_name(table, column)
                and _rule(existing)
            ):
                continue
            pending.append((column, referred, existing))

        if pending:
            _replace(table, pending, RULE)


def downgrade():
    inspector = inspect(op.get_bind())

    for table in reversed(_tables()):
        reflected = _reflected(inspector, table)
        pending = [
            (column, referred, reflected[column])
            for fk_table, column, referred in FOREIGN_KEYS
            if fk_table == table and column in reflected and _rule(reflected[column])
        ]

        if pending:
            _replace(table, pending, None)
