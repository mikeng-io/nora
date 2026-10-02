"""cognition_wake_outcome records what the session spent: input, output and
cache-read tokens, and its root trace id.

`nora_cognition_unit_tokens_total` is a Prometheus counter, and nora restarts two
to twenty-four times a day (measured 2026-09-29 to 10-02), so every restart zeroes
it and a unit-day's spend is not readable from it. The outcome row is the one row
a session leaves at its END: `cognition_session` is written before the model loop
and is append-only, so a token column on it would need an in-place update. The
columns are NULLable and NULL means unread, never zero (a wake that ended before a
model call, one a restart ended, a provider that reported no usage). No row is
rewritten and nothing is backfilled: `cognition_wake_outcome` is append-only
(10 §8), so an outcome written before this migration reads NULL.

Numbered 0090 on purpose: 0088 is `fix-delete-withdraws` (#711) and 0089 is the
live ledger (the Cognition cut-over's PR-2), neither merged when this was written.
`down_revision` names the head of THIS tree (`0087_document_state`); whoever merges
this after either of them re-points it at the then head, and `uv run alembic heads`
must print exactly ONE line afterwards.

Revision ID: 0090_cognition_outcome_tokens
Revises: 0087_document_state
Create Date: 2026-10-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0090_cognition_outcome_tokens"
down_revision = "0087_document_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cognition_wake_outcome", sa.Column("input_tokens", sa.BigInteger))
    op.add_column("cognition_wake_outcome", sa.Column("output_tokens", sa.BigInteger))
    op.add_column("cognition_wake_outcome", sa.Column("cache_read_tokens", sa.BigInteger))
    op.add_column("cognition_wake_outcome", sa.Column("trace_id", sa.Text))


def downgrade() -> None:
    # Going back down forgets what every session since cost. Nothing else is lost.
    op.drop_column("cognition_wake_outcome", "trace_id")
    op.drop_column("cognition_wake_outcome", "cache_read_tokens")
    op.drop_column("cognition_wake_outcome", "output_tokens")
    op.drop_column("cognition_wake_outcome", "input_tokens")
