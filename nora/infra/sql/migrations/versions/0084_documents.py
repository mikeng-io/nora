"""documents, document_revision — what Nora publishes to the portal (ADR 0020,
design/artifact/00-design.md §5).

1. `documents`: the identity of a document, written once (platform, room, creator;
   `recorded_at` is when it first existed). Never updated.
2. `document_revision`: one full snapshot per revision (title, the checked spec as
   jsonb, the search text, the sha256 of its canonical form, the full sha256 of every
   picture it names, who wrote it). Never updated. The CURRENT document is the highest
   revision, so there is no head pointer to drift and no row that changes in place;
   `(document_id, revision)` is the primary key, which is what lets two concurrent
   revisions from the same base resolve to one winner and one `conflict`. There is no
   foreign key to `documents`: a restore copies append-only tables in no promised
   order (see `nora/infra/sql/schema.py`).

Both tables are append-only, so neither gets the `nora_row_history` trigger:
`nora.infra.sql.history.APPEND_ONLY` names `recorded_at` as their system time, and
`tests/infra/test_row_history.py` is the guard that every table is classified once.

Follows `0083_computer_exports` (#649). `uv run alembic heads` must print exactly ONE line.

Revision ID: 0084_documents
Revises: 0083_computer_exports
Create Date: 2026-10-01
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0084_documents"
down_revision = "0083_computer_exports"
branch_labels = None
depends_on = None


def _recorded_at() -> sa.Column[Any]:
    return sa.Column(
        "recorded_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("clock_timestamp()"),
    )


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("platform", sa.Text, nullable=False),
        sa.Column("conversation_id", sa.Text, nullable=False),
        sa.Column("created_by", sa.Text, nullable=False),
        _recorded_at(),
    )
    op.create_index("idx_documents_room", "documents", ["platform", "conversation_id"])
    op.create_index("idx_documents_creator", "documents", ["created_by"])

    op.create_table(
        "document_revision",
        sa.Column("document_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("spec", pg.JSONB, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("sha256", sa.Text, nullable=False),
        sa.Column(
            "media",
            pg.ARRAY(sa.Text),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column("written_by", sa.Text, nullable=False),
        _recorded_at(),
        sa.PrimaryKeyConstraint("document_id", "revision", name="pk_document_revision"),
        sa.CheckConstraint("revision >= 1", name="ck_document_revision_positive"),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_document_revision_sha256"),
        sa.CheckConstraint(
            "array_to_string(media, ',') ~ '^([0-9a-f]{64}(,[0-9a-f]{64})*)?$'",
            name="ck_document_revision_media",
        ),
        sa.CheckConstraint(
            "octet_length(spec::text) <= 1048576", name="ck_document_revision_spec_size"
        ),
    )


def downgrade() -> None:
    op.drop_table("document_revision")
    op.drop_index("idx_documents_creator", table_name="documents")
    op.drop_index("idx_documents_room", table_name="documents")
    op.drop_table("documents")
