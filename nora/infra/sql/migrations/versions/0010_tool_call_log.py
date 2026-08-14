"""tool_call_log — one row per successful cacheable tool call
(tool-recall-log spec §2, §7).

Revision ID: 0010_tool_call_log
Revises: 0009_compute_job_hashes
Create Date: 2026-08-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0010_tool_call_log"
down_revision = "0009_compute_job_hashes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tool_call_log",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("conversation_id", sa.Text, nullable=False),
        sa.Column("conversation_type", sa.Text, nullable=False),
        sa.Column("tag_id", sa.Text, nullable=False),
        sa.Column("tool_name", sa.Text, nullable=False),
        sa.Column("tool_input", pg.JSONB, nullable=False),
        sa.Column("tool_result", pg.JSONB, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # Hand-written, not `op.create_index` from the ORM `sa.Index` object —
    # `... DESC` needs raw DDL the same way `idx_messages_conv_ts`'s own
    # migration does (see that migration for precedent if unsure of the
    # exact form alembic expects here).
    op.execute(
        "CREATE INDEX idx_tool_call_log_conv_ts ON tool_call_log "
        "(conversation_id, created_at DESC)"
    )


def downgrade() -> None:
    op.drop_index("idx_tool_call_log_conv_ts", table_name="tool_call_log")
    op.drop_table("tool_call_log")
