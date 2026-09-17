"""participation_window_state / tag_debounce_state -- the two triggers that
decide whether Nora speaks, keeping their cursors across a restart.

**THE SAME DEFECT AS `0050_dream_schedule_state`, IN TWO MORE PLACES.** That
migration's own docstring records what a process-local clock costs: a
function-local `dict[str, float]` stamped with `time.monotonic()`, a container
force-recreated twice inside two hours on 2026-09-06, and a "daily" adaptor
that was really "once per deploy". `nora.app.participation.ParticipationTrigger`
and `nora.tag.Debounce` were the same class of state, unfixed:

* `_last_send_at` is the participation SEND cooldown (design 35 §10.3). Lost on
  every recreate, so she could speak again immediately after a restart however
  recently she had spoken -- the one piece of state whose whole job is to cap
  what a room HEARS.
* `_last_window_end` is the watermark that makes a judged window spent. Lost
  with it, so a window already answered could be judged a second time.
* `Debounce._last` is R-9, the per-conversation tag rate limiter. Lost, so a
  duplicate tag inside the window ran a second turn.

**ONLY THE COLD-PATH STATE IS STORED, and that is what makes this safe.**
`note_message`'s docstring is explicit that it is called from the dispatcher
and must not await anything, so the three cursors it moves per MESSAGE
(`_last_message_at`, `_unjudged`, `_conversation_type`) get no column here. Two
of the three are re-derived at hydration from `messages`, which already holds
them; `_unjudged` is NOT, because a `messages`-derived count cannot reproduce
it -- a tagged message and a voice note are rows in that table that never reach
`note_message` -- and an over-counted volume arm closes windows EARLIER than
the trigger does today, which is a behaviour change wearing a persistence
change's clothes. It keeps starting at zero after a restart, exactly as it does
today. See `nora/app/participation.py`.

Every column written here is written once per window close, once per send, or
once per admitted tag.

**TWO TABLES, NOT ONE.** `nora/tag.py` is a leaf module that imports nothing --
no config, no store, no domain type -- and the engagement path's rate limiter
and the participation path's watermarks have different keys' lifetimes,
different write cadences and different bounds (see `schema.py`'s comments on
each). A shared `trigger_state` table with a `kind` column would be one table
answering two questions, which is the shape `0050` rejected for
`consolidation_watermark` for the same reason.

**EPOCH MILLISECONDS ON THE WALL CLOCK, as BIGINT.** `messages.ts` and
`dream_schedule_state.last_fired_at` are both that, and both for this reason: a
`monotonic()` value has an arbitrary per-process epoch, so the number a restart
reads back is not a time at all. Both modules' default clock moves from
`time.monotonic` to `time.time` in the same commit -- storing a monotonic value
would have produced garbage that still typechecks.

**NO BACKFILL, AND NONE IS POSSIBLE.** Neither dict ever persisted anything, so
there is no prior state to carry. Every room starts absent, which the readers
map to «never» rather than to 1970 -- the same first-sighting behaviour as
today, once, and then a cadence that survives the next deploy.

Revision ID: 0056_speech_trigger_state
Revises: 0055_culture
Create Date: 2026-09-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0056_speech_trigger_state"
down_revision = "0055_culture"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "participation_window_state",
        sa.Column("platform", sa.Text, primary_key=True),
        sa.Column("conversation_id", sa.Text, primary_key=True),
        # NOT NULL: a row exists because a window closed, so the value that
        # created the row is the value it carries.
        sa.Column("last_window_end_at", sa.BigInteger, nullable=False),
        # NULLABLE, and the null is load-bearing: «she has never spoken here».
        # A `server_default` of 0 here would make a brand-new room look like one
        # that last spoke in 1970 -- which permits the send, the same answer a
        # correct read gives, so the bug would survive its own test.
        sa.Column("last_send_at", sa.BigInteger),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_table(
        "tag_debounce_state",
        sa.Column("platform", sa.Text, primary_key=True),
        sa.Column("conversation_id", sa.Text, primary_key=True),
        sa.Column("last_allowed_at", sa.BigInteger, nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("tag_debounce_state")
    op.drop_table("participation_window_state")
