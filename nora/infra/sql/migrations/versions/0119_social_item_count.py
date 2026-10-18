"""social_item_count: a post's or reel's likes, comments and reposts as its
author's grid shows them; social_poll: the `recount` stage.

design/cognition-loop/22-social-stream.md §5. APPEND-ONLY: a read appends a
row for an item only where its counts differ from the item's latest row, so
the latest row is the current counts and the rows before it are how they
moved. Each count is NULL when the read did not carry it (unknown, never 0);
`likes_hidden` is the author's switch, and `ck_social_item_count_hidden`
keeps `likes` NULL beside it. `read_id` is the `social_poll` row of the read
that carried them. A `recount` row is one re-read of a member's first page
for its counts and places. No foreign key to `social_item`, like
`social_item_state`.

Follows `0118_place_area`. `uv run alembic heads` must print exactly ONE line.

Revision ID: 0119_social_item_count
Revises: 0118_place_area
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0119_social_item_count"
down_revision = "0118_place_area"
branch_labels = None
depends_on = None

_STAGES = "'probe', 'story', 'items', 'item', 'backfill', 'relist'"


def _stages(stages: str) -> None:
    op.drop_constraint("ck_social_poll_stage", "social_poll", type_="check")
    op.create_check_constraint("ck_social_poll_stage", "social_poll", f"stage IN ({stages})")


def upgrade() -> None:
    op.create_table(
        "social_item_count",
        sa.Column("id", pg.UUID(as_uuid=False), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("network", sa.Text, nullable=False),
        sa.Column("item_id", sa.Text, nullable=False),
        sa.Column("likes", sa.BigInteger),
        sa.Column("comments", sa.BigInteger),
        sa.Column("reposts", sa.BigInteger),
        sa.Column("likes_hidden", sa.Boolean, nullable=False),
        sa.Column("read_id", pg.UUID(as_uuid=False)),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.CheckConstraint(
            "likes IS NULL OR NOT likes_hidden", name="ck_social_item_count_hidden",
        ),
    )
    op.create_index(
        "idx_social_item_count_item", "social_item_count",
        ["network", "item_id", "recorded_at"],
    )
    _stages(f"{_STAGES}, 'recount'")


def downgrade() -> None:
    # The older code knows no recount row; the counts go with their table.
    op.execute("DELETE FROM social_poll WHERE stage = 'recount'")
    _stages(_STAGES)
    op.drop_index("idx_social_item_count_item", table_name="social_item_count")
    op.drop_table("social_item_count")
