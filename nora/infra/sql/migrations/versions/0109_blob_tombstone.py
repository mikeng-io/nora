"""blob_tombstone: what deleting a story frame's bytes leaves behind.

design/cognition-loop/22-social-stream.md §5 and 70-migration.md §9. A story
frame's media bytes are deleted at their bound; this APPEND-ONLY table keeps,
per deletion, the content address, the kind of thing the bytes were, why and
when they went, so a restore to an earlier time knows it is incomplete for
them. The same bytes can be stored again and deleted again, so an address can
have several rows; a row is about the rows naming the bytes written before it.

Follows `0108_social_perception`. `uv run alembic heads` must print exactly
ONE line.

Revision ID: 0109_blob_tombstone
Revises: 0108_social_perception
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0109_blob_tombstone"
down_revision = "0108_social_perception"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "blob_tombstone",
        sa.Column("id", pg.UUID(as_uuid=False), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("sha256", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_blob_tombstone_sha256"),
        sa.CheckConstraint("kind IN ('story_frame')", name="ck_blob_tombstone_kind"),
        sa.CheckConstraint(
            "reason IN ('embedded', 'hard_limit')", name="ck_blob_tombstone_reason",
        ),
    )
    op.create_index(
        "idx_blob_tombstone_sha", "blob_tombstone", ["sha256", "recorded_at"],
    )


def downgrade() -> None:
    # Going back down forgets which bytes were deleted; the bytes stay gone.
    op.drop_table("blob_tombstone")
