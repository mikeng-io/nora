"""cognition_wake_outcome.cause admits 'none': a session that CLOSED failed for no
reason (m2 of the 2026-09-30 cognition-loop review, DEC-245).

0080 gave every outcome a cause NOT NULL DEFAULT 'infra', so a `closed` wake
read `cause = 'infra'`: «the world failed» on a session that succeeded, and
every count of infra failures had to remember to filter on the outcome. A wake
that closed (or was cancelled by a shutdown) now records `none`. The check
gains the value. `cognition_wake_outcome` is APPEND_ONLY (10 §8), so this migration
rewrites NO row: a closed or cancelled outcome written before it keeps its
`infra`, and every reader normalises on read (`nora.domain.stimuli.stored_cause`,
applied in `wake_state`), because that cause was never a fact.

Revision ID: 0085_cognition_cause_none
Revises: 0084_documents
Create Date: 2026-09-30
"""

from __future__ import annotations

from alembic import op

revision = "0085_cognition_cause_none"
down_revision = "0084_documents"
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


def downgrade() -> None:
    # No UPDATE (append-only): NOT VALID leaves rows written with `none` alone
    # and still refuses one in a new row.
    op.drop_constraint(
        "ck_cognition_wake_outcome_cause", "cognition_wake_outcome", type_="check",
    )
    op.create_check_constraint(
        "ck_cognition_wake_outcome_cause", "cognition_wake_outcome",
        "cause IN ('infra', 'agenda')", postgresql_not_valid=True,
    )
