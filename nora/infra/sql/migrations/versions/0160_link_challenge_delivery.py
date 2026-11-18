"""Persist each side's private link challenge alongside its request."""

import sqlalchemy as sa
from alembic import op

revision = "0160_link_challenge_delivery"
down_revision = "0159_otp_account_proof"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "managed_link_delivery",
        sa.Column("request_id", sa.Text, nullable=False),
        sa.Column("side", sa.Text, nullable=False),
        sa.Column("nonce", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["request_id"], ["managed_link_request.request_id"],
            name="managed_link_delivery_request_fkey", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("request_id", "side",
                                name="pk_managed_link_delivery"),
        sa.CheckConstraint(
            "side IN ('initiating','receiving')",
            name="ck_managed_link_delivery_side",
        ),
    )


def downgrade() -> None:
    op.drop_table("managed_link_delivery")
