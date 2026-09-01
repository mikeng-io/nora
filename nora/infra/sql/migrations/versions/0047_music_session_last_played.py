"""music_session.last_played_* -- the autoplay re-seed seed for boot-time resume
(design/discord-music/08-autoplay.md, R-67).

**Why a column and not the now_playing_* trio.** When autoplay's chain ends
(related() answered empty, or every Mix track was already played this session),
`_advance` persists the session with now_playing_* all NULL -- the row still
exists because the voice connection is live, but nothing is playing. A restart
then finds nothing to resume, and `resume_persisted_session` falls through to
`on_track_finished()` with `previous=None`, so autoplay can never fire: there
is no seed. The last-played trio records the most recent track that ACTUALLY
started, so a boot-time resume with an idle-but-live session can re-seed the
autoplay chain from it instead of sitting silent.

**NULLABLE, and NULL is the only "never played".** A session that has never
played a track (created by a play() that only queued, or a fresh join) has no
seed and must not fabricate one -- the same argument migration 0037 made for
`consumed_verdict_tag`.

**NO BACKFILL, deliberately.** Existing rows get NULL, which means "no
last-played on record" -- the first autoplay pick after the upgrade seeds from
whatever actually plays next, exactly as before. Backfilling from now_playing
would be wrong: now_playing_* NULL means nothing was playing, and guessing a
seed from an empty record is exactly the fabrication this column exists to
avoid.

Revision ID: 0047_music_session_last_played
Revises: 0046_standing_last_folded_row_id
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0047_music_session_last_played"
down_revision = "0046_standing_last_folded_row_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("music_session", sa.Column("last_played_source", sa.Text, nullable=True))
    op.add_column("music_session", sa.Column("last_played_locator", sa.Text, nullable=True))
    op.add_column("music_session", sa.Column("last_played_title", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("music_session", "last_played_title")
    op.drop_column("music_session", "last_played_locator")
    op.drop_column("music_session", "last_played_source")
