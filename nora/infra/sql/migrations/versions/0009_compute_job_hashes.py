"""compute_jobs gains task_sha256/context_sha256 — provenance for a job's own
inputs (Nora Compute context-channel spec §5).

Revision ID: 0009_compute_job_hashes
Revises: 0008_compute_jobs
Create Date: 2026-08-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_compute_job_hashes"
down_revision = "0008_compute_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # `server_default=""` mirrors 0008's own `state` column (nullable=False,
    # server_default="pending") — it backstops any row that predates this
    # migration, but every row `make_artifact` creates from here on writes a
    # REAL hash explicitly (nora/agent/tools.py), the same way every row
    # already writes a real `state` despite the column having a default.
    op.add_column(
        "compute_jobs",
        sa.Column("task_sha256", sa.Text, nullable=False, server_default=""),
    )
    op.add_column(
        "compute_jobs",
        sa.Column("context_sha256", sa.Text, nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("compute_jobs", "context_sha256")
    op.drop_column("compute_jobs", "task_sha256")
