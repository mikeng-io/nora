"""social_perception, social_item.read_id, and one `social` stimulus per read.

design/cognition-loop/22-social-stream.md §5, §7. `social_perception` is
APPEND-ONLY: one row per attempt to perceive an item's media, with the units
it was charged to, how many vision calls it spent and why it ended, so the per-unit daily
cap is a sum over the day's rows and survives a restart. `at` is the
perception pass's own clock (the day the cap counts in); `recorded_at` is the
system time. `social_item.read_id` names the read that recorded an item, so
the stimulus a read raises can wait for that read's items alone. A `social`
stimulus is unique on `(unit, ref)`, like `time` and `rollup` (0071): a pass
that announces the same read twice writes it once.

Follows `0107_social_stream`. `uv run alembic heads` must print exactly ONE
line.

Revision ID: 0108_social_perception
Revises: 0107_social_stream
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0108_social_perception"
down_revision = "0107_social_stream"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("social_item", sa.Column("read_id", pg.UUID(as_uuid=False)))
    op.create_index("idx_social_item_read", "social_item", ["read_id"])
    op.create_table(
        "social_perception",
        sa.Column("id", pg.UUID(as_uuid=False), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("network", sa.Text, nullable=False),
        sa.Column("item_id", sa.Text, nullable=False),
        sa.Column("person", sa.Text, nullable=False),
        sa.Column("units", pg.ARRAY(sa.Text), nullable=False,
                  server_default=sa.text("'{}'")),
        sa.Column("origin", sa.Text, nullable=False),
        sa.Column("outcome", sa.Text, nullable=False),
        sa.Column("calls", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("reason", sa.Text),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.CheckConstraint(
            "origin IN ('poll', 'backfill')", name="ck_social_perception_origin",
        ),
        sa.CheckConstraint(
            "outcome IN ('ok', 'nothing_found', 'unavailable', 'failed', 'capped')",
            name="ck_social_perception_outcome",
        ),
        sa.CheckConstraint("calls >= 0", name="ck_social_perception_calls"),
    )
    op.create_index(
        "idx_social_perception_item", "social_perception", ["network", "item_id", "at"],
    )
    op.create_index("idx_social_perception_at", "social_perception", ["at"])
    op.create_index(
        "uq_stimulus_social_ref", "stimulus", ["unit", "ref"], unique=True,
        postgresql_where=sa.text("kind = 'social'"),
    )


def downgrade() -> None:
    # Going back down forgets every perception attempt and which read recorded
    # an item; the items, their media and the stimuli raised stay.
    op.drop_index("uq_stimulus_social_ref", table_name="stimulus")
    op.drop_index("idx_social_perception_at", table_name="social_perception")
    op.drop_index("idx_social_perception_item", table_name="social_perception")
    op.drop_table("social_perception")
    op.drop_index("idx_social_item_read", table_name="social_item")
    op.drop_column("social_item", "read_id")
