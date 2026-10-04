"""The `subagent` actor on the computer audit (design/subagent §4, step 2).

A background worker a tag turn launched makes computer calls under its own actor, so the
audit says who made each one (`computer_events.actor`, path `subagent`). Widens
`ck_computer_events_actor` and `ck_computer_sessions_actor` (both are built from the one
vocabulary in `nora.domain.computer_events.ACTORS`) with `subagent`; a worker never opens a
session, so the second is widened only so the two stay one vocabulary.

Downgrade narrows both NOT VALID (a `subagent` row written meanwhile stays; a new one is
refused).

Follows `0094_compute_jobs_generic_outbox`. `uv run alembic heads` must print exactly ONE line.

Revision ID: 0095_computer_actor_subagent
Revises: 0094_compute_jobs_generic_outbox
Create Date: 2026-10-04
"""

from __future__ import annotations

from alembic import op

revision = "0095_computer_actor_subagent"
down_revision = "0094_compute_jobs_generic_outbox"
branch_labels = None
depends_on = None

_TAIL = "OR (starts_with(actor, 'dreamer:') AND length(actor) > 8)"
_BEFORE = f"actor IN ('cognition', 'fire', 'operator', 'sweeper', 'tag') {_TAIL}"
_AFTER = f"actor IN ('cognition', 'fire', 'operator', 'subagent', 'sweeper', 'tag') {_TAIL}"
_CHECKS = (
    ("computer_events", "ck_computer_events_actor"),
    ("computer_sessions", "ck_computer_sessions_actor"),
)


def upgrade() -> None:
    for table, name in _CHECKS:
        op.drop_constraint(name, table, type_="check")
        op.create_check_constraint(name, table, _AFTER)


def downgrade() -> None:
    for table, name in _CHECKS:
        op.drop_constraint(name, table, type_="check")
        op.create_check_constraint(name, table, _BEFORE, postgresql_not_valid=True)
