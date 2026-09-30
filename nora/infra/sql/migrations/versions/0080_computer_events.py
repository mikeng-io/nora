"""computer_events — the computer audit log, one row per computer tool call
on every return path (design/sandbox-runtime §12, DEC-243).

Append-only (`nora.infra.sql.history.APPEND_ONLY`, system time
`created_at`), the `0010_tool_call_log` shape. `detail` is JSONB: the
command or path in full, output lengths and a 500-character head of stdout
and stderr, never a command's whole output.

Revision ID: 0080_computer_events
Revises: 0079_cognition_pace_remind
Create Date: 2026-09-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0080_computer_events"
down_revision = "0079_cognition_pace_remind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "computer_events",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("platform", sa.Text, nullable=False),
        sa.Column("conversation_id", sa.Text, nullable=False),
        sa.Column("conversation_type", sa.Text, nullable=False),
        sa.Column("tag_id", sa.Text, nullable=False),
        sa.Column("scope", sa.Text, nullable=False),
        sa.Column("tool", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("exit_code", sa.Integer, nullable=True),
        sa.Column("duration_ms", sa.Integer, nullable=True),
        sa.Column(
            "detail", pg.JSONB, nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_index(
        "idx_computer_events_conv_ts", "computer_events",
        ["conversation_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_computer_events_conv_ts", table_name="computer_events")
    op.drop_table("computer_events")
