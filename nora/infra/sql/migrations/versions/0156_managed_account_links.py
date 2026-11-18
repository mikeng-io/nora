"""Persist pending bilateral consent independently of published bindings."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0156_managed_account_links"
down_revision = "0155_managed_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "managed_link_request",
        sa.Column("request_id", sa.Text, primary_key=True),
        sa.Column("snapshot", pg.JSONB, nullable=False),
    )
    op.create_table(
        "managed_link_decision",
        sa.Column("operation_id", sa.Text, primary_key=True),
        sa.Column("request_id", sa.Text, nullable=False),
        sa.Column("effect_id", sa.Text, nullable=False),
        sa.Column("evidence", pg.JSONB, nullable=False),
        sa.ForeignKeyConstraint(
            ["request_id"], ["managed_link_request.request_id"],
            name="managed_link_decision_request_fkey", ondelete="RESTRICT",
        ),
    )
    op.create_index("idx_managed_link_decision_request", "managed_link_decision", ["request_id"])


def downgrade() -> None:
    op.drop_index("idx_managed_link_decision_request", table_name="managed_link_decision")
    op.drop_table("managed_link_decision")
    op.drop_table("managed_link_request")
