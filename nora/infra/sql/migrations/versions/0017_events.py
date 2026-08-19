"""events + event_rsvps

Revision ID: 0017_events
Revises: 0016_geocode_cache
Create Date: 2026-08-19

Hand-written (CLAUDE.md: `idx_messages_conv_ts` is the one index
autogenerate must never touch; neither table here is that index, but this
migration is hand-written anyway to stay consistent with every other
table-adding revision in this repo and to keep the CHECK constraints exact).

Originally authored as `0015_events` against a fork point that predated two
other migrations landing on `origin/master` concurrently
(`0015_presence_analytics`, `0016_geocode_cache`) — renumbered to `0017` and
rechained onto `0016_geocode_cache` at merge time. See
`docs/superpowers/specs/2026-08-19-event-module-design.md` §2 for the full
history.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_events"
down_revision: str | None = "0016_geocode_cache"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("conversation_id", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("created_by", sa.Text, nullable=False),
        sa.Column("when", sa.DateTime(timezone=True), nullable=True),
        sa.Column("venue_text", sa.Text, nullable=True),
        sa.Column("venue_place_id", sa.Text, nullable=True),
        sa.Column("venue_name", sa.Text, nullable=True),
        sa.Column("venue_address", sa.Text, nullable=True),
        sa.Column("venue_rating", sa.Float, nullable=True),
        sa.Column(
            "venue_price_level", sa.Text, nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column("venue_maps_url", sa.Text, nullable=True),
        sa.Column("venue_resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("reminder_id", sa.Uuid, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('proposed','confirmed','cancelled')",
            name="ck_events_status",
        ),
    )
    op.create_index(
        "idx_events_conversation", "events", ["conversation_id", "status"],
    )
    op.create_table(
        "event_rsvps",
        sa.Column(
            "event_id", sa.Uuid, sa.ForeignKey("events.id"), primary_key=True,
        ),
        sa.Column("person_id", sa.Text, primary_key=True),
        sa.Column("response", sa.Text, nullable=False),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "response IN ('yes','no','maybe')", name="ck_event_rsvps_response",
        ),
    )


def downgrade() -> None:
    op.drop_table("event_rsvps")
    op.drop_index("idx_events_conversation", table_name="events")
    op.drop_table("events")
