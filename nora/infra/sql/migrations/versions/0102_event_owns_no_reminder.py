"""event_owns_no_reminder — retire the reminder an event minted for itself.

DEC-303 (supersedes DEC-139's event-owned reminder). An event with a time used to
mint a `kind = 'gathering'` reminder due AT that time, and its fire turn told
Nora a person had asked to be reminded, which was false for every event a
Cognition session proposed. A reminder is now always a person's request or
Nora's own `remind`; nothing writes `events.reminder_id` and no fire path
renders a `gathering` row.

Data only: every PENDING `gathering` reminder is cancelled (outcome
`cancelled_event_owned_retired`) and every `events.reminder_id` is cleared. The
column stays, so a downgrade has nothing to restore; the cancelled rows are not
revived (their fire path is gone).

Follows `0101_self_tension`. `uv run alembic heads` must print exactly ONE line.

Revision ID: 0102_event_owns_no_reminder
Revises: 0101_self_tension
Create Date: 2026-10-09
"""

from __future__ import annotations

from alembic import op

revision = "0102_event_owns_no_reminder"
down_revision = "0101_self_tension"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE reminders SET state = 'cancelled', "
        "outcome = 'cancelled_event_owned_retired' "
        "WHERE kind = 'gathering' AND state IN ('pending', 'firing')"
    )
    op.execute("UPDATE events SET reminder_id = NULL WHERE reminder_id IS NOT NULL")


def downgrade() -> None:
    """Nothing to restore: the column was never dropped, and a cancelled
    reminder of a retired kind has no fire path to come back to."""
