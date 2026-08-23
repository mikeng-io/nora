"""music_queue — the Discord music bot's upcoming-queue, the one thing that
survives a restart (design/discord-music/03-state-and-persistence.md)

Now-playing/playback position is deliberately NOT here — in-memory only on
`MusicPlayer`, lost on a restart or a Discord Gateway reconnect, same
document §"Two different lifetimes". Only the upcoming queue is persisted.

HAND-WRITTEN, same reason `0004_reminders` is: this table carries a CHECK
constraint (`ck_music_queue_position_non_negative`) `alembic check` cannot
see. The column list and its order match
`nora.infra.sql.schema.MUSIC_QUEUE` exactly; the parity gate fails on any
drift.

Revision ID: 0028_music_queue
Revises: 0027_fact_candidate_provenance
Create Date: 2026-08-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0028_music_queue"
down_revision = "0027_fact_candidate_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "music_queue",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("guild_id", sa.Text, nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("added_by_person_id", sa.Text, nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        # The constraint `alembic check` cannot see — the whole reason this
        # revision is hand-written.
        sa.CheckConstraint("position >= 0", name="ck_music_queue_position_non_negative"),
    )
    op.create_index("idx_music_queue_guild_position", "music_queue", ["guild_id", "position"])


def downgrade() -> None:
    op.drop_index("idx_music_queue_guild_position", table_name="music_queue")
    op.drop_table("music_queue")
