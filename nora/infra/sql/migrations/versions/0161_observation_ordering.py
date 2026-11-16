"""The durable per-source roster observation mark (`identity.md`)."""

import sqlalchemy as sa
from alembic import op

revision = "0161_observation_ordering"
down_revision = "0160_link_challenge_delivery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "managed_observation",
        sa.Column("service", sa.Text, primary_key=True),
        sa.Column("collection", sa.Text, primary_key=True),
        sa.Column("scope_kind", sa.Text, primary_key=True),
        sa.Column("scope_id", sa.Text, primary_key=True),
        sa.Column("observed_ms", sa.BigInteger, nullable=False),
        sa.Column("operation_id", sa.Text, nullable=False),
        sa.Column("effect", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint("scope_kind IN ('workspace','unit','room')",
                           name="ck_managed_observation_scope_kind"),
    )


def downgrade() -> None:
    op.drop_table("managed_observation")
