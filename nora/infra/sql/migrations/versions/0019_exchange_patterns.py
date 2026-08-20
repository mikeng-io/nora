"""exchange patterns: evidence + consolidated table (Dreamer Plan 2).

Shares `consolidation_watermark` with Plan 1 (`consolidator_name="exchange"`),
so no new watermark table. Both new tables are empty at every deployment this
migration reaches, so their indexes are plain `CREATE INDEX`, not
`CONCURRENTLY` (contrast `0013_room_language`'s `idx_messages_dream_lag`,
built against an already-live, already-growing `messages` table).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0019_exchange_patterns"
down_revision = "0018_presence_rhythm"
branch_labels = None
depends_on = None

_EXCHANGE_ACTS = (
    "insult_trade", "banter", "tease", "callback_joke",
    "agreement_ritual", "disagreement_ritual", "compliment_exchange",
)
_ACT_CHECK = "act IN " + str(_EXCHANGE_ACTS)


def upgrade() -> None:
    op.create_table(
        "exchange_observation",
        sa.Column("id", sa.Integer, sa.Identity(always=False), primary_key=True),
        sa.Column("scope_id", sa.Text, nullable=False),
        sa.Column("conversation_id", sa.Text, nullable=False),
        sa.Column("act", sa.Text, nullable=False),
        sa.Column("message_ids", pg.ARRAY(sa.Integer), nullable=False),
        sa.Column("participants", pg.ARRAY(sa.Text), nullable=False),
        sa.Column("observed_at", sa.BigInteger, nullable=False),
        sa.Column("extractor_confidence", sa.Float, nullable=False),
        sa.UniqueConstraint("scope_id", "act", "message_ids"),
        sa.CheckConstraint(_ACT_CHECK, name="exchange_observation_act_check"),
        sa.CheckConstraint(
            "array_length(message_ids, 1) >= 2",
            name="exchange_observation_message_ids_check",
        ),
        sa.CheckConstraint(
            "array_length(participants, 1) >= 2",
            name="exchange_observation_participants_check",
        ),
    )
    op.create_index(
        "idx_exchange_observation_scope", "exchange_observation", ["scope_id"],
    )
    op.create_table(
        "room_exchange",
        sa.Column("scope_id", sa.Text, primary_key=True),
        sa.Column("act", sa.Text, primary_key=True),
        sa.Column("participants", pg.ARRAY(sa.Text), primary_key=True),
        sa.Column("first_seen_at", sa.BigInteger, nullable=False),
        sa.Column("last_seen_at", sa.BigInteger, nullable=False),
        sa.Column("occurrence_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("recognition_score", sa.Float, nullable=False, server_default=sa.text("0")),
        sa.Column("state", sa.Text, nullable=False, server_default=sa.text("'live'")),
        sa.Column("examples", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.CheckConstraint(_ACT_CHECK, name="room_exchange_act_check"),
        sa.CheckConstraint("state IN ('live','fading','stale')", name="room_exchange_state_check"),
        sa.CheckConstraint(
            "array_length(participants, 1) >= 2", name="room_exchange_participants_check",
        ),
    )


def downgrade() -> None:
    op.drop_table("room_exchange")
    op.drop_index("idx_exchange_observation_scope", table_name="exchange_observation")
    op.drop_table("exchange_observation")
