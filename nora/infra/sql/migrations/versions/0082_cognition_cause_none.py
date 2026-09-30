"""cognition_wake_outcome.cause admits 'none': a session that CLOSED failed for no
reason (m2 of the 2026-09-30 cognition-loop review, DEC-245).

0080 gave every outcome a cause NOT NULL DEFAULT 'infra', so a `closed` wake
read `cause = 'infra'`: «the world failed» on a session that succeeded, and
every count of infra failures had to remember to filter on the outcome. A wake
that closed (or was cancelled by a shutdown) now records `none`. The check
gains the value; rows written before this migration keep what they carry, except
that the closed and cancelled ones are set to `none` here, because their cause
was never a fact (`nora.domain.stimuli.wake_state` reads a closed wake's cause
nowhere, so no decision changes).

Revision ID: 0082_cognition_cause_none
Revises: 0081_computer_events
Create Date: 2026-09-30
"""

from __future__ import annotations

from alembic import op

revision = "0082_cognition_cause_none"
down_revision = "0081_computer_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_cognition_wake_outcome_cause", "cognition_wake_outcome", type_="check",
    )
    op.create_check_constraint(
        "ck_cognition_wake_outcome_cause", "cognition_wake_outcome",
        "cause IN ('infra', 'agenda', 'none')",
    )
    op.execute(
        "UPDATE cognition_wake_outcome SET cause = 'none' "
        "WHERE outcome IN ('closed', 'cancelled') AND cause = 'infra'"
    )


def downgrade() -> None:
    op.execute("UPDATE cognition_wake_outcome SET cause = 'infra' WHERE cause = 'none'")
    op.drop_constraint(
        "ck_cognition_wake_outcome_cause", "cognition_wake_outcome", type_="check",
    )
    op.create_check_constraint(
        "ck_cognition_wake_outcome_cause", "cognition_wake_outcome",
        "cause IN ('infra', 'agenda')",
    )
