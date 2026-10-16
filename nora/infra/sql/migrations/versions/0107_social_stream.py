"""social_item, social_poll, social_item_state — the social stream's Record.

design/cognition-loop/22-social-stream.md §5. Three APPEND-ONLY tables: a
`social_item` is one story frame, post or reel, unique on `(network, item_id)`
so a second insert of the same item does nothing (the dedupe) and carrying no
unit column; a `social_poll` row is one probe or one read; a `social_item_state`
row says a frame is gone or hidden. Nothing is updated, so no `nora_row_history`
trigger: `nora.infra.sql.history.APPEND_ONLY` names `recorded_at` as each
table's system time. No foreign keys, for the reason `0084_documents` gives.

Follows `0106_memory_vectors`. `uv run alembic heads` must print exactly ONE
line.

Revision ID: 0107_social_stream
Revises: 0106_memory_vectors
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0107_social_stream"
down_revision = "0106_memory_vectors"
branch_labels = None
depends_on = None


def _recorded_at() -> sa.Column[Any]:
    return sa.Column(
        "recorded_at", sa.DateTime(timezone=True), nullable=False,
        server_default=sa.text("clock_timestamp()"),
    )


def upgrade() -> None:
    op.create_table(
        "social_item",
        sa.Column("id", pg.UUID(as_uuid=False), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("network", sa.Text, nullable=False),
        sa.Column("item_id", sa.Text, nullable=False),
        sa.Column("person", sa.Text, nullable=False),
        sa.Column("account", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("caption", sa.Text),
        sa.Column("alt_text", sa.Text),
        sa.Column("location", sa.Text),
        sa.Column("tagged", pg.ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("mentioned", pg.ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("media_ref", sa.Text),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("origin", sa.Text, nullable=False),
        sa.Column("collab", sa.Boolean, nullable=False, server_default=sa.text("false")),
        _recorded_at(),
        sa.CheckConstraint(
            "kind IN ('story_frame', 'post', 'reel', 'thread_post')",
            name="ck_social_item_kind",
        ),
        sa.CheckConstraint("origin IN ('poll', 'backfill')", name="ck_social_item_origin"),
        sa.CheckConstraint(
            "kind <> 'story_frame' OR expires_at IS NOT NULL",
            name="ck_social_item_story_frame_expires",
        ),
        sa.CheckConstraint("length(btrim(item_id)) > 0", name="ck_social_item_has_an_id"),
    )
    op.create_index(
        "uq_social_item_key", "social_item", ["network", "item_id"], unique=True,
    )
    op.create_index(
        "idx_social_item_person", "social_item", ["network", "person", "taken_at"],
    )
    op.create_table(
        "social_poll",
        sa.Column("id", pg.UUID(as_uuid=False), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("network", sa.Text, nullable=False),
        sa.Column("person", sa.Text, nullable=False),
        sa.Column("stage", sa.Text, nullable=False),
        sa.Column("outcome", sa.Text, nullable=False),
        sa.Column("latest_reel_media", sa.BigInteger),
        sa.Column("media_count", sa.Integer),
        sa.Column("total_clips_count", sa.Integer),
        sa.Column("advanced_marker", sa.Boolean, nullable=False,
                  server_default=sa.text("false")),
        sa.Column("latency_ms", sa.Integer),
        _recorded_at(),
        sa.CheckConstraint(
            "stage IN ('probe', 'story', 'items', 'item')", name="ck_social_poll_stage",
        ),
        sa.CheckConstraint(
            "outcome IN ('unchanged', 'story_due', 'items_due', 'partial', 'count_fell', "
            "'read_ok', 'halted', 'backed_off', 'error')",
            name="ck_social_poll_outcome",
        ),
    )
    op.create_index(
        "idx_social_poll_person", "social_poll", ["network", "person", "recorded_at"],
    )
    op.create_table(
        "social_item_state",
        sa.Column("id", pg.UUID(as_uuid=False), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("network", sa.Text, nullable=False),
        sa.Column("item_id", sa.Text, nullable=False),
        sa.Column("state", sa.Text, nullable=False),
        sa.Column("reason", sa.Text),
        _recorded_at(),
        sa.CheckConstraint("state IN ('gone', 'hidden')", name="ck_social_item_state_state"),
    )
    op.create_index(
        "idx_social_item_state_item", "social_item_state",
        ["network", "item_id", "recorded_at"],
    )


def downgrade() -> None:
    # Going back down forgets every recorded item, poll and state; the rest of
    # the Record is untouched.
    op.drop_index("idx_social_item_state_item", table_name="social_item_state")
    op.drop_table("social_item_state")
    op.drop_index("idx_social_poll_person", table_name="social_poll")
    op.drop_table("social_poll")
    op.drop_index("idx_social_item_person", table_name="social_item")
    op.drop_index("uq_social_item_key", table_name="social_item")
    op.drop_table("social_item")
