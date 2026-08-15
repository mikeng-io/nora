"""sticker_index + media_descriptions — the two-level media dedup cache
(sticker spec §4.1).

Revision ID: 0012_sticker_cache
Revises: 0010_tool_call_log
Create Date: 2026-08-15

Numbered 0012, not 0011: 0011 was claimed by both this branch and an
in-flight presence-speed migration, each chaining off 0010 — two revisions,
one parent, alembic multi-head. This branch yields the number; 0011 stays
reserved for the presence work when it lands. down_revision stays 0010, not
0011, on purpose — 0011 exists nowhere but as an untracked file on another
checkout, and alembic resolves the revision graph, not the filename, so
pointing at an uncommitted revision would break every other checkout with
"Can't locate revision". Chaining to 0010 keeps master a single line
(0010 → 0012) whether or not 0011 ever lands.

Whoever lands 0011_presence_speed: chain its down_revision onto
0012_sticker_cache, not onto 0010 — the reservation only holds if the
eventual migration links to what's actually on master. Chaining onto 0010
recreates the exact two-head problem this rename fixed, just pointed the
other way.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_sticker_cache"
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
