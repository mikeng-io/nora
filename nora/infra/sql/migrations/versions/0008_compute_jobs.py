"""compute_jobs — the Nora Compute outbox (Nora Compute spec §4, §12).

Revision ID: 0008_compute_jobs
Revises: 0007_drop_artifacts
Create Date: 2026-08-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0008_compute_jobs"
down_revision = "0007_drop_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "compute_jobs",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("backend_job_id", sa.Text, nullable=False),
        sa.Column("task", sa.Text, nullable=False),
        sa.Column("artifact_id", sa.Text, nullable=True),
        sa.Column("platform", sa.Text, nullable=False),
        sa.Column("conversation_id", sa.Text, nullable=False),
        sa.Column("conversation_type", sa.Text, nullable=False),
        sa.Column("requester_person_id", sa.Text, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("state", sa.Text, nullable=False, server_default="pending"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_url", sa.Text, nullable=True),
        sa.Column("result_summary", sa.Text, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.CheckConstraint(
            "state IN ('pending', 'firing', 'done', 'failed')",
            name="ck_compute_jobs_state",
        ),
    )
    op.create_index("idx_compute_jobs_state", "compute_jobs", ["state"])


def downgrade() -> None:
    op.drop_index("idx_compute_jobs_state", table_name="compute_jobs")
    op.drop_table("compute_jobs")
