"""document_state — a document's lifecycle: archived, deleted, restored (ADR 0020,
design/artifact/00-design.md §5).

One APPEND-ONLY table. Each row is one change of state (`active`, `archived`,
`deleted`) for one document, with who asked (`set_by`) and when (`recorded_at`).
A document's current state is the row with the highest `id` for it; a document with
no row is `active`, so every document written before this migration is active and
nothing is backfilled. `documents` and `document_revision` are not touched: a
deleted document keeps every revision, and a restore is one more row.

Append-only, so no `nora_row_history` trigger: `nora.infra.sql.history.APPEND_ONLY`
names `recorded_at` as its system time. No foreign key to `documents`, for the
reason `0084_documents` gives (a restore copies append-only tables in no order).

Follows `0086_event_citations`. `uv run alembic heads` must print exactly ONE line.

Revision ID: 0087_document_state
Revises: 0086_event_citations
Create Date: 2026-10-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0087_document_state"
down_revision = "0086_event_citations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_state",
        sa.Column("id", sa.Integer, sa.Identity(always=False), primary_key=True),
        sa.Column("document_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("state", sa.Text, nullable=False),
        sa.Column("set_by", sa.Text, nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.CheckConstraint(
            "state IN ('active', 'archived', 'deleted')", name="ck_document_state_state"
        ),
        sa.CheckConstraint("length(set_by) > 0", name="ck_document_state_set_by"),
    )
    op.create_index("idx_document_state_latest", "document_state", ["document_id", "id"])


def downgrade() -> None:
    # Going back down forgets every archive and delete: every document reads as active
    # again under the old code, which has no notion of state. Nothing else is lost.
    op.drop_index("idx_document_state_latest", table_name="document_state")
    op.drop_table("document_state")
