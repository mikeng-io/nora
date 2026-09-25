"""reminders_proactive_open_note -- at most one open proactive row per note
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

This fails, and changes nothing, on a database that already holds two open
proactive rows on one note. That is deliberate: which of them to keep is the
operator's call, never a migration's. Production held none on 2026-09-25.

Revision ID: 0064_reminders_proactive_open_note
Revises: 0063_note_merge_decision
Create Date: 2026-09-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0064_reminders_proactive_open_note"
down_revision = "0063_note_merge_decision"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
