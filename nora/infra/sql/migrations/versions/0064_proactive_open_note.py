"""proactive_open_note -- at most one open proactive row per note
(issue #158).

The proactive adaptor booked the same note's reminder twice in production:
decided 23:01 and again 01:01 for the same 10:00, and 07:02 and again 18:04
for the same 17:00, each second row written while the first was still
`pending`. The only per-note check was the wake clock, which charges a strike
once per Hong Kong day and never looked at the row being written.

A PARTIAL unique index on the note id a proactive row carries in `payload`,
over the states in which that row is still going to be said (`pending`) or is
being said (`firing`). A row that fired, failed or was cancelled holds nothing
open, so a re-raise after silence is still writable.

THE VOCABULARY IS WRITTEN OUT rather than read from `nora.domain.reminders`,
following `0053_decision_log`: a migration is a frozen historical statement.

EXISTING DUPLICATES ARE RESOLVED FIRST, because this runs at app start
(`run_migrations` in the composition root): an index that could not be built
over a duplicate would stop Nora booting, and the old image keeps writing
duplicates (26% of note-linked proactive rows, measured 2026-09-25) until the
new one is live, so a pre-check on the day proves nothing at deploy time.
Production held none open on 2026-09-25; this is for the gap. For each note
with more than one open proactive row, ONE is kept and the rest are cancelled:

    WITH ranked AS (
      SELECT id, row_number() OVER (
        PARTITION BY payload ->> 'note_id'
        ORDER BY (state = 'firing'), due_at, created_at, id
      ) AS n
      FROM reminders
      WHERE kind = 'proactive' AND state IN ('pending', 'firing')
        AND payload ->> 'note_id' IS NOT NULL
    )
    UPDATE reminders SET state = 'cancelled',
      outcome = 'cancelled_duplicate_note', fired_at = now()
    FROM ranked WHERE reminders.id = ranked.id AND ranked.n > 1

- The EARLIEST-DUE row is kept: it is the decision that would have spoken
  first, and every later copy would only have repeated it (in production the
  copies fell due at the same moment and rode one fire turn together).
- A `pending` row is kept before a `firing` one. Migrations run at boot, before
  boot recovery (`sweep_firing`) fails every row a dead process left `firing`;
  keeping that one would leave the note with nothing.
- `cancelled_duplicate_note` follows the fire-time screen's `cancelled_*`
  outcomes (`cancelled_note_retired`, `cancelled_note_moved`), and `fired_at`
  is stamped as they stamp it: the moment the row was HANDLED. The decisions
  block renders that outcome, so her own log says why the row went.

The count is logged (`alembic.runtime.migration`). The downgrade drops the
index and does not un-cancel: which copy should have spoken is not knowable
afterwards.

Revision ID: 0064_proactive_open_note
Revises: 0063_note_merge_decision
Create Date: 2026-09-25
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

# 24 characters: `alembic_version.version_num` is varchar(32), and the
# first name for this file (`0064_reminders_proactive_open_note`, 34) failed
# every migration in the gate.
revision = "0064_proactive_open_note"
down_revision = "0063_note_merge_decision"
branch_labels = None
depends_on = None


log = logging.getLogger("alembic.runtime.migration")

_RESOLVE_DUPLICATES = sa.text("""
    WITH ranked AS (
      SELECT id, row_number() OVER (
        PARTITION BY payload ->> 'note_id'
        ORDER BY (state = 'firing'), due_at, created_at, id
      ) AS n
      FROM reminders
      WHERE kind = 'proactive' AND state IN ('pending', 'firing')
        AND payload ->> 'note_id' IS NOT NULL
    )
    UPDATE reminders SET state = 'cancelled',
      outcome = 'cancelled_duplicate_note', fired_at = now()
    FROM ranked WHERE reminders.id = ranked.id AND ranked.n > 1
""")


def upgrade() -> None:
    cancelled = op.get_bind().execute(_RESOLVE_DUPLICATES).rowcount
    log.info(
        "0064: cancelled %d duplicate open proactive reminder(s) before "
        "creating uq_reminders_proactive_open_note", cancelled,
    )
    op.create_index(
        "uq_reminders_proactive_open_note", "reminders",
        [sa.text("(payload ->> 'note_id')")],
        unique=True,
        postgresql_where=sa.text(
            "kind = 'proactive' AND state IN ('pending', 'firing')"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_reminders_proactive_open_note", table_name="reminders")
