"""cognition_wake_outcome.cause -- why a session FAILED (DEC-244, #637 second
review, major).

Five consecutive failures used to strike every stimulus the failing wakes
claimed, so a ~90-minute provider outage retracted real human messages. Each
failed outcome now records its CAUSE when it is written: `infra` (the world
failed: provider, network, database, a kill, a timeout, anything unclassified)
backs the unit off and never strikes; `agenda` (the agenda itself cannot be
processed: context overflow, a reply cut at max_tokens, a deterministic
grounding fault) is the only cause that counts toward a strike.

The column is NOT NULL with the default `infra`, so every row written before
this migration reads as `infra`: history can never strike anything after the
fact. A wake with NO outcome row (a kill, a crash: EXPIRED) has no cause and is
read as `infra` by `nora.domain.stimuli.wake_state`.

Revision ID: 0080_cognition_outcome_cause
Revises: 0079_cognition_pace_remind
Create Date: 2026-09-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0080_cognition_outcome_cause"
down_revision = "0079_cognition_pace_remind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cognition_wake_outcome",
        sa.Column("cause", sa.Text, nullable=False, server_default=sa.text("'infra'")),
    )
    op.create_check_constraint(
        "ck_cognition_wake_outcome_cause", "cognition_wake_outcome",
        "cause IN ('infra', 'agenda')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_cognition_wake_outcome_cause", "cognition_wake_outcome", type_="check",
    )
    op.drop_column("cognition_wake_outcome", "cause")
