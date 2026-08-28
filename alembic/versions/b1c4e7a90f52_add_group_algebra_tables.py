"""add group algebra tables

Additive only (SEC-H1 / H1-1). Creates the group, edge, closure, membership
and grant tables and adds equipment.group_id. Nothing reads any of it yet —
scoping still runs on the legacy path strings until H1-5 cuts over, and those
columns are not dropped until H1-11.

Revision ID: b1c4e7a90f52
Revises: 4acc9d5f6339
Create Date: 2026-08-21 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b1c4e7a90f52'
down_revision: Union[str, Sequence[str], None] = '4acc9d5f6339'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'groups',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('kind', sa.String(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    # Unique INDEX, not a table-level UniqueConstraint: the model declares
    # unique=True alongside index=True, which SQLAlchemy emits as a single
    # unique index. Declaring a constraint here instead enforces the same rule
    # by a different object, which --autogenerate then reports as drift forever.
    op.create_index(op.f('ix_groups_name'), 'groups', ['name'], unique=True)

    op.create_table(
        'group_edges',
        sa.Column('parent_id', sa.Integer(), nullable=False),
        sa.Column('child_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['parent_id'], ['groups.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['child_id'], ['groups.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('parent_id', 'child_id'),
    )
    op.create_index('ix_group_edges_child_id', 'group_edges', ['child_id'], unique=False)

    op.create_table(
        'group_closure',
        sa.Column('ancestor_id', sa.Integer(), nullable=False),
        sa.Column('descendant_id', sa.Integer(), nullable=False),
        sa.Column('depth', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['ancestor_id'], ['groups.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['descendant_id'], ['groups.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('ancestor_id', 'descendant_id'),
    )
    op.create_index('ix_group_closure_descendant_id', 'group_closure', ['descendant_id'], unique=False)

    op.create_table(
        'group_memberships',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('group_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['group_id'], ['groups.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id', 'group_id'),
    )
    op.create_index('ix_group_memberships_group_id', 'group_memberships', ['group_id'], unique=False)

    op.create_table(
        'grants',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('group_id', sa.Integer(), nullable=False),
        sa.Column('capability', sa.String(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['group_id'], ['groups.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'capability', 'group_id', name='uq_grants_user_group_capability'),
    )
    op.create_index(op.f('ix_grants_group_id'), 'grants', ['group_id'], unique=False)

    # Batch mode is required here: SQLite cannot ALTER a table to add a foreign
    # key, and a plain add_column() with an inline ForeignKey does not emit an
    # inline REFERENCES clause — Alembic issues a separate ADD CONSTRAINT, which
    # the SQLite dialect refuses. Batch mode copy-and-moves the table on SQLite
    # and is a straight ALTER on Postgres, which is what CI runs.
    with op.batch_alter_table('equipment', schema=None) as batch_op:
        batch_op.add_column(sa.Column('group_id', sa.Integer(), nullable=True))
        batch_op.create_index(op.f('ix_equipment_group_id'), ['group_id'], unique=False)
        batch_op.create_foreign_key('fk_equipment_group_id', 'groups', ['group_id'], ['id'])


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('equipment', schema=None) as batch_op:
        batch_op.drop_constraint('fk_equipment_group_id', type_='foreignkey')
        batch_op.drop_index(op.f('ix_equipment_group_id'))
        batch_op.drop_column('group_id')

    op.drop_index(op.f('ix_grants_group_id'), table_name='grants')
    op.drop_table('grants')

    op.drop_index('ix_group_memberships_group_id', table_name='group_memberships')
    op.drop_table('group_memberships')

    op.drop_index('ix_group_closure_descendant_id', table_name='group_closure')
    op.drop_table('group_closure')

    op.drop_index('ix_group_edges_child_id', table_name='group_edges')
    op.drop_table('group_edges')

    op.drop_index(op.f('ix_groups_name'), table_name='groups')
    op.drop_table('groups')
