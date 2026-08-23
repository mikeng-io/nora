"""music_session — one row per guild, a SNAPSHOT of what `MusicPlayer` holds
in memory: now-playing, paused, volume, which channels
(design/discord-music/06-session-persistence.md).

Distinct from `music_queue` (0028): that's an append/pop LOG, one row per
upcoming track. This is a snapshot, upserted in place -- `guild_id` is the
primary key itself, never a separate UUID plus a uniqueness constraint,
because there is exactly one row per guild by construction.

HAND-WRITTEN, same reason 0028/0004 are: `ck_music_session_volume_range`
(a CHECK constraint) is invisible to `alembic check`. The column list and
its order match `nora.infra.sql.schema.MUSIC_SESSION` exactly; the parity
gate fails on any drift.

Revision ID: 0029_music_session
Revises: 0028_music_queue
Create Date: 2026-08-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0029_music_session"
down_revision = "0028_music_queue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "music_session",
        sa.Column("guild_id", sa.Text, primary_key=True),
        sa.Column("voice_channel_id", sa.Text, nullable=False),
        sa.Column("notify_channel_id", sa.Text, nullable=False),
        sa.Column("now_playing_source", sa.Text, nullable=True),
        sa.Column("now_playing_locator", sa.Text, nullable=True),
        sa.Column("now_playing_title", sa.Text, nullable=True),
        sa.Column("paused", sa.Boolean, nullable=False),
        sa.Column("volume", sa.Float, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        # The constraint `alembic check` cannot see -- the whole reason
        # this revision is hand-written.
        sa.CheckConstraint(
            "volume >= 0.0 AND volume <= 2.0", name="ck_music_session_volume_range",
        ),
    )


def downgrade() -> None:
    op.drop_table("music_session")
