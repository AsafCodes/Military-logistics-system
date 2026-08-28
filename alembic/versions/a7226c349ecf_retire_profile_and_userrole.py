"""retire profile and userrole

Destructive (SEC-H1 / H1-12). Drops the permission-matrix representation of
authority now that no router has consulted it since H1-10: the `profiles`
table, `users.profile_id`, `users.role`, and the two hierarchy columns H1-11
left in place because they still had a live reader (`users.battalion`,
`users.company` -- UserResponse reshapes away from them in this same entry,
so the schema and the response change together instead of twice).

Unlike H1-11 there is no backfill: nothing downstream is derived from any of
these four columns, so there is no successor to feed before they go.

Revision ID: a7226c349ecf
Revises: c93f2a615d84
Create Date: 2026-08-24 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a7226c349ecf'
down_revision: Union[str, Sequence[str], None] = 'c93f2a615d84'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    No data statement precedes this one -- see the module docstring. `users`
    goes first, in its own batch, so its FK to `profiles` is gone before the
    table it pointed at is dropped; `drop_table` doesn't need that ordering on
    SQLite (foreign keys are unenforced unless PRAGMA foreign_keys=ON, and even
    then batch mode rebuilds `users` without the constraint), but it costs
    nothing to leave no window where a live FK points at a table that no
    longer exists.
    """
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('profile_id')
        batch_op.drop_column('role')
        batch_op.drop_column('battalion')
        batch_op.drop_column('company')

    op.drop_index(op.f('ix_profiles_id'), table_name='profiles')
    op.drop_table('profiles')


def downgrade() -> None:
    """Downgrade schema -- the shape returns, the permission matrix does not.

    Same posture as c93f2a615d84's downgrade: recreating a dropped column or
    table recreates it empty. No reverse migration reconstructs which profile
    a user held or what each profile's booleans were -- that data is gone the
    moment upgrade() runs. This exists so the chain stays reversible for
    schema purposes, not to promise a restore.
    """
    op.create_table(
        'profiles',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(), nullable=True),
        sa.Column('name_he', sa.String(), nullable=True),
        sa.Column('can_view_all_equipment', sa.Boolean(), nullable=True),
        sa.Column('can_view_battalion_inventory', sa.Boolean(), nullable=True),
        sa.Column('can_view_battalion_realtime', sa.Boolean(), nullable=True),
        sa.Column('can_view_company_realtime', sa.Boolean(), nullable=True),
        sa.Column('can_change_maintenance_status', sa.Boolean(), nullable=True),
        sa.Column('can_mark_as_defective', sa.Boolean(), nullable=True),
        sa.Column('can_assign_equipment', sa.Boolean(), nullable=True),
        sa.Column('can_change_assignment_others', sa.Boolean(), nullable=True),
        sa.Column('can_assign_roles', sa.Boolean(), nullable=True),
        sa.Column('can_add_category', sa.Boolean(), nullable=True),
        sa.Column('can_add_specific_item', sa.Boolean(), nullable=True),
        sa.Column('can_remove_category', sa.Boolean(), nullable=True),
        sa.Column('can_remove_specific_item', sa.Boolean(), nullable=True),
        sa.Column('can_manage_locations', sa.Boolean(), nullable=True),
        sa.Column('can_generate_battalion_report', sa.Boolean(), nullable=True),
        sa.Column('can_generate_company_report', sa.Boolean(), nullable=True),
        sa.Column('holds_equipment', sa.Boolean(), nullable=True),
        sa.Column('must_report_presence', sa.Boolean(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
    )
    op.create_index(op.f('ix_profiles_id'), 'profiles', ['id'], unique=False)

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('company', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('battalion', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('role', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('profile_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_users_profile_id', 'profiles', ['profile_id'], ['id']
        )
