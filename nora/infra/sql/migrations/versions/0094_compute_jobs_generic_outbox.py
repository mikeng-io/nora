"""compute_jobs becomes the generic deferred-job outbox (design/subagent §6, step 1).

Adds `kind` (which backend owns the row; every existing row is `legacy`, already
terminal, and no backend answers to it), `tag_id` (the launching turn, for the trace
link) and `failure_reason` (why a job failed: lost, a limit stop, a timeout), and widens
`ck_compute_jobs_state` with `cancelled`.

Also `payload` (JSONB NOT NULL, the typed launch payload in full, `{}` for existing rows, so a
brief is re-derivable beyond its hashes), `result_report` and `result_document_ids` (what
`mark` writes from the job's result, readable after delivery) and `trace_id` (the worker's
Langfuse trace, written by a backend when known; nothing sets it yet).

`kind` has no server default after the backfill: a producer names its kind or the insert
fails. System-time history needs nothing here: `nora_row_history` records `to_jsonb(x)`
of the whole row, so the new columns are in every later version.

Downgrade drops the added columns and narrows the check NOT VALID (a `cancelled` row
written meanwhile stays; a new one is refused).

Follows `0093_drop_withdrawal_leftovers`. `uv run alembic heads` must print exactly ONE line.

Revision ID: 0094_compute_jobs_generic_outbox
Revises: 0093_drop_withdrawal_leftovers
Create Date: 2026-10-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0094_compute_jobs_generic_outbox"
down_revision = "0093_drop_withdrawal_leftovers"
branch_labels = None
depends_on = None

_CK = "ck_compute_jobs_state"


def upgrade() -> None:
    op.add_column(
        "compute_jobs",
        sa.Column("kind", sa.Text, nullable=False, server_default="legacy"),
    )
    op.alter_column("compute_jobs", "kind", server_default=None)
    op.add_column(
        "compute_jobs",
        sa.Column(
            "payload", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.alter_column("compute_jobs", "payload", server_default=None)
    op.add_column("compute_jobs", sa.Column("result_report", sa.Text, nullable=True))
    op.add_column("compute_jobs", sa.Column("result_document_ids", pg.JSONB, nullable=True))
    op.add_column("compute_jobs", sa.Column("trace_id", sa.Text, nullable=True))
    op.add_column("compute_jobs", sa.Column("tag_id", sa.Text, nullable=True))
    op.add_column("compute_jobs", sa.Column("failure_reason", sa.Text, nullable=True))
    op.drop_constraint(_CK, "compute_jobs", type_="check")
    op.create_check_constraint(
        _CK, "compute_jobs",
        "state IN ('pending', 'firing', 'done', 'failed', 'cancelled')",
    )


def downgrade() -> None:
    op.drop_constraint(_CK, "compute_jobs", type_="check")
    op.create_check_constraint(
        _CK, "compute_jobs", "state IN ('pending', 'firing', 'done', 'failed')",
        postgresql_not_valid=True,
    )
    for column in ("trace_id", "result_document_ids", "result_report", "payload"):
        op.drop_column("compute_jobs", column)
    op.drop_column("compute_jobs", "failure_reason")
    op.drop_column("compute_jobs", "tag_id")
    op.drop_column("compute_jobs", "kind")
