"""room language: watermark, observations, lexicon.

`idx_messages_dream_lag` is built CONCURRENTLY inside an `autocommit_block()`,
and both halves of that are required rather than cautious. `messages` is written
by every inbound turn and — since `store.prune_enabled = false` — grows without
bound, so a plain CREATE INDEX takes an ACCESS EXCLUSIVE lock and stalls ingest
for the length of the build. CONCURRENTLY cannot run inside a transaction, and
`migrations/env.py` runs migrations inside one (`context.begin_transaction()`),
so the autocommit block is what makes it legal. This is the first use of
`autocommit_block()` in this repo — there is no precedent to copy, only Alembic's
own contract.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0013_room_language"
down_revision = "0012_sticker_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "consolidation_watermark",
        sa.Column("consolidator_name", sa.Text, primary_key=True),
        sa.Column("scope_id", sa.Text, primary_key=True),
        sa.Column("last_message_id", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_table(
        "language_observation",
        sa.Column("id", sa.Integer, sa.Identity(always=False), primary_key=True),
        sa.Column("scope_id", sa.Text, nullable=False),
        sa.Column("conversation_id", sa.Text, nullable=False),
        sa.Column("message_id", sa.Integer, nullable=False),
        sa.Column("person_id", sa.Text),
        sa.Column("surface_form", sa.Text, nullable=False),
        sa.Column("canonical_form", sa.Text, nullable=False),
        sa.Column("sense", sa.Text, nullable=False),
        sa.Column("part_of_speech", sa.Text),
        sa.Column("context", sa.Text, nullable=False),
        sa.Column("observed_at", sa.BigInteger, nullable=False),
        sa.Column("extractor_confidence", sa.Float, nullable=False),
        sa.UniqueConstraint("message_id", "canonical_form", "sense"),
    )
    op.create_table(
        "room_lexeme",
        sa.Column("scope_id", sa.Text, primary_key=True),
        sa.Column("canonical_form", sa.Text, primary_key=True),
        sa.Column("sense", sa.Text, primary_key=True),
        sa.Column("first_seen_at", sa.BigInteger, nullable=False),
        sa.Column("last_seen_at", sa.BigInteger, nullable=False),
        sa.Column("occurrence_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("distinct_speakers", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("recognition_score", sa.Float, nullable=False, server_default=sa.text("0")),
        sa.Column("state", sa.Text, nullable=False, server_default=sa.text("'live'")),
        sa.Column("examples", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.CheckConstraint("state IN ('live','fading','stale')"),
    )
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_messages_dream_lag "
            "ON messages (conversation_id, id) WHERE is_nora = 0"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_messages_dream_lag")
    op.drop_table("room_lexeme")
    op.drop_table("language_observation")
    op.drop_table("consolidation_watermark")
