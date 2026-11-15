"""Keep identity publication unavailable until each owning operation agrees."""

import sqlalchemy as sa
from alembic import op

revision = "0157_managed_identity_holds"
down_revision = "0156_managed_account_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "managed_identity_hold",
        sa.Column("person_id", sa.Text, primary_key=True),
        sa.Column("operation_id", sa.Text, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["person_id"], ["managed_person.person_id"],
            name="managed_identity_hold_person_fkey", ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("managed_identity_hold")
