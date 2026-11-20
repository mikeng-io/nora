"""Track each independent support behind a membership, preserving provenance."""

import sqlalchemy as sa
from alembic import op

revision = "0158_membership_supports"
down_revision = "0157_managed_identity_holds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "managed_membership_support",
        sa.Column("person_id", sa.Text, primary_key=True),
        sa.Column("scope_kind", sa.Text, primary_key=True),
        sa.Column("scope_id", sa.Text, primary_key=True),
        sa.Column("source", sa.Text, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["person_id"], ["managed_person.person_id"],
            name="managed_membership_support_person_fkey", ondelete="RESTRICT"),
    )
    # Every standing membership begins supported by the source that wrote it.
    op.execute(
        "INSERT INTO managed_membership_support "
        "(person_id, scope_kind, scope_id, source) "
        "SELECT person_id, scope_kind, scope_id, source "
        "FROM managed_membership WHERE state = 'member' "
        "ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    op.drop_table("managed_membership_support")
