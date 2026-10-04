"""compute_jobs keeps a sub-agent's checkpoint (design/subagent §4d).

Adds `checkpoint` (JSONB, NULL: the worker's four lists plus, written by code, the files it
kept) and `checkpoint_at` (when the worker last wrote its lists). Both nullable with no
backfill: every existing row, and every job that writes no checkpoint, reads as having none,
which every reader renders as nothing. System-time history needs nothing here:
`nora_row_history` records `to_jsonb(x)` of the whole row, so the new columns are in every
later version.

Downgrade drops both columns.

Follows `0095_computer_actor_subagent`. `uv run alembic heads` must print exactly ONE line.

Revision ID: 0096_compute_jobs_checkpoint
Revises: 0095_computer_actor_subagent
Create Date: 2026-10-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0096_compute_jobs_checkpoint"
down_revision = "0095_computer_actor_subagent"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("compute_jobs", sa.Column("checkpoint", pg.JSONB, nullable=True))
    op.add_column(
        "compute_jobs", sa.Column("checkpoint_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("compute_jobs", "checkpoint_at")
    op.drop_column("compute_jobs", "checkpoint")
