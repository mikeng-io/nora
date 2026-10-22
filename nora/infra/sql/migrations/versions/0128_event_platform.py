"""events.platform: the durable platform half of an event's owner.

A row carried `unit_id` but never the platform, so a legacy `unit_id=""` row's
owner was inferred from the CURRENT `policy.toml` assignment. Removing that
assignment and giving the room to another unit then handed the historical
event to the new caller. The row's own `(unit_id, platform)` is the owner now:
`event_in_scope` requires both, and a row with an empty platform is
QUARANTINED.

No backfill, on purpose. Current policy is not evidence of what the policy was
when a row was written, so there is no value this migration could set without
inventing history. Existing rows keep the empty default (preserved, not
deleted) and are invisible to every scoped read until an operator establishes
ownership explicitly. New writes always carry the platform from the trusted
caller.

Follows `0127_character_content`. `uv run alembic heads` must print exactly
ONE line.

Revision ID: 0128_event_platform
Revises: 0127_character_content
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0128_event_platform"
down_revision = "0127_character_content"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column("platform", sa.Text, nullable=False, server_default=sa.text("''")),
    )


def downgrade() -> None:
    # Drops the persisted owner; the quarantine marker goes with it, so the
    # older code would again infer a legacy row's owner from current policy.
    op.drop_column("events", "platform")
