"""sticker_index + media_descriptions — the two-level media dedup cache
(sticker spec §4.1).

Revision ID: 0011_sticker_cache
Revises: 0010_tool_call_log
Create Date: 2026-08-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_sticker_cache"
down_revision = "0010_tool_call_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sticker_index",
        sa.Column("platform", sa.Text, primary_key=True),
        sa.Column("fetch_token", sa.Text, primary_key=True),
        sa.Column("sha256", sa.Text, nullable=False),
        sa.Column("media_type", sa.Text, nullable=False, server_default=""),
        sa.Column("size", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "first_seen", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_seen", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("hits", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_table(
        "media_descriptions",
        sa.Column("sha256", sa.Text, primary_key=True),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("model", sa.Text, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("media_descriptions")
    op.drop_table("sticker_index")
