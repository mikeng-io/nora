"""instagram_summons -- the public summons ledger
(design/instagram-adoption/19, `nora.app.summons`).

One row per comment-mention the summons poller handled, UNIQUE on the comment
id: the row is claimed (`INSERT ... ON CONFLICT (comment_id) DO NOTHING`)
before the model runs, so a summons is answered at most once however many
processes or ticks see it. `mode` records which brake was in force, so a
dry-run `staged` row is never retried as a live one. `flushing` is the live
write's lock: one flush takes a `staged` row to `flushing` before its POST,
and a row a dead process left there is swept to `ambiguous`, never re-posted.

THE VOCABULARY IS WRITTEN OUT rather than read from `nora.domain.summons`,
following `0053_decision_log`: a migration is a frozen historical statement.

Parent: `0065_standing_working_set` (#541), which landed on master first;
this was re-pointed onto it at merge time. `uv run alembic heads` prints one
line (AGENTS.md, "Never assume your migration's parent is unclaimed").

Revision ID: 0066_instagram_summons
Revises: 0065_standing_working_set
Create Date: 2026-09-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0066_instagram_summons"
down_revision = "0065_standing_working_set"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "instagram_summons",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("comment_id", sa.Text, nullable=False, unique=True),
        sa.Column("media_id", sa.Text, nullable=False),
        sa.Column("shortcode", sa.Text, nullable=False),
        sa.Column("author_igid", sa.Text, nullable=False),
        sa.Column("person_id", sa.Text, nullable=True),
        sa.Column("mention_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "seen_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("state", sa.Text, nullable=False),
        sa.Column("mode", sa.Text, nullable=False),
        sa.Column("reply_text", sa.Text, nullable=True),
        sa.Column("reply_comment_id", sa.Text, nullable=True),
        sa.Column("bridge_code", sa.Text, nullable=True),
        sa.Column("trace_id", sa.Text, nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "state IN ('claimed', 'ignored_summoner', 'stale', 'unreadable', "
            "'silent', 'staged', 'flushing', 'published', 'refused', 'capped', "
            "'duplicate', 'ambiguous', 'failed')",
            name="ck_instagram_summons_state",
        ),
        sa.CheckConstraint(
            "mode IN ('dry_run', 'live')",
            name="ck_instagram_summons_mode",
        ),
    )
    op.create_index(
        "idx_instagram_summons_state", "instagram_summons", ["state", "mode"],
    )


def downgrade() -> None:
    op.drop_index("idx_instagram_summons_state", table_name="instagram_summons")
    op.drop_table("instagram_summons")
