"""drop the legacy hierarchy columns

Destructive (SEC-H1 / H1-11). Removes the path-string representation of "unit"
now that nothing reads it, and tightens equipment.group_id to NOT NULL, which
every write path has treated as mandatory since H1-6 while the schema called it
optional.

Four columns go: users.unit_hierarchy, users.unit_path, equipment.unit_hierarchy
and locations.unit_path. users.battalion and users.company stay -- they are the
only two of the six with a live reader (UserResponse, and the dashboard label
under the user's name), and they are dropped by H1-12 together with the rest of
that schema rather than reshaping UserResponse twice.

Revision ID: c93f2a615d84
Revises: b1c4e7a90f52
Create Date: 2026-08-24 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy import text

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c93f2a615d84'
down_revision: Union[str, Sequence[str], None] = 'b1c4e7a90f52'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Every data operation happens before the first DDL statement, deliberately.
    The check below can refuse, and a refusal must leave the schema untouched
    rather than half-rebuilt -- SQLite's batch mode is a drop-and-rename, so
    aborting midway through it is not a place to be.
    """
    conn = op.get_bind()

    # The last moment this mapping exists. The seeded path strings ARE the group
    # names -- '188/53/A' on both sides -- so an item that was placed before
    # H1-4 backfilled group_id can still be placed now, from the column that is
    # about to be destroyed. After this revision there is no source left.
    conn.execute(text(
        "UPDATE equipment SET group_id = ("
        "  SELECT g.id FROM groups g WHERE g.name = equipment.unit_hierarchy"
        ") WHERE group_id IS NULL"
    ))

    # Anything still unplaced is a fact for an operator, not a guess for a
    # migration. An item belonging to no group is visible to no commander under
    # the new model, and inventing a placement to make the constraint pass is
    # precisely the shape SEC-H4 named: an authority decision made somewhere
    # nobody will look for it. Refuse, and name the rows.
    orphans = conn.execute(text(
        "SELECT id, serial_number FROM equipment WHERE group_id IS NULL ORDER BY id"
    )).all()
    if orphans:
        listed = ", ".join(f"#{row[0]} ({row[1] or 'no serial'})" for row in orphans)
        raise RuntimeError(
            "Refusing to migrate: equipment belongs to no group and no legacy "
            "path names one, so these rows would be visible to nobody:\n  "
            + listed
            + "\n\nPlace them by setting equipment.group_id to a row in `groups`, "
            "then re-run. This revision drops the only column that could have "
            "answered the question for you."
        )

    with op.batch_alter_table('equipment', schema=None) as batch_op:
        batch_op.drop_index(op.f('ix_equipment_unit_hierarchy'))
        batch_op.drop_column('unit_hierarchy')
        batch_op.alter_column(
            'group_id', existing_type=sa.Integer(), nullable=False
        )

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(op.f('ix_users_unit_hierarchy'))
        batch_op.drop_column('unit_hierarchy')
        batch_op.drop_column('unit_path')

    with op.batch_alter_table('locations', schema=None) as batch_op:
        batch_op.drop_column('unit_path')


def downgrade() -> None:
    """Downgrade schema -- the shape returns, the data does not.

    Recreating a dropped column recreates an empty column. The path strings
    themselves are gone the moment upgrade() runs, and no reverse migration can
    reconstruct them: group names are the same strings today only because the
    seed wrote both, which is not a property the schema enforces. This exists so
    the chain stays reversible for schema purposes, not to promise a restore.
    """
    with op.batch_alter_table('locations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('unit_path', sa.String(), nullable=True))

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('unit_path', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('unit_hierarchy', sa.String(), nullable=True))
        batch_op.create_index(op.f('ix_users_unit_hierarchy'), ['unit_hierarchy'], unique=False)

    with op.batch_alter_table('equipment', schema=None) as batch_op:
        batch_op.alter_column(
            'group_id', existing_type=sa.Integer(), nullable=True
        )
        batch_op.add_column(sa.Column('unit_hierarchy', sa.String(), nullable=True))
        batch_op.create_index(op.f('ix_equipment_unit_hierarchy'), ['unit_hierarchy'], unique=False)
