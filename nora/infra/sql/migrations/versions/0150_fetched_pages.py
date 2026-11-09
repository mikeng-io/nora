"""fetched_pages — one conversation's materialized copy of one url.

`web_fetch` fetches a page ONCE, seals the bytes into the shared blob store
(rustfs) and writes this index row: the conversation that asked, the url,
the content address and the expiry. `offset`/`about` reads then answer from
the stored copy instead of a second provider call. `provenance` carries the
provider-side facts the served slices keep (`provider`, `saved_copy`,
`truncated`, `images`).

The composite primary key IS the dedup: a repeated fetch of the same url in
the same conversation replaces the row. Expiry is lazy — an expired row
reads as a miss and the next fetch writes over it.

Revision ID: 0150_fetched_pages
Revises: 0149_computer_profile_merge
Create Date: 2026-11-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0150_fetched_pages"
down_revision = "0149_computer_profile_merge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fetched_pages",
        sa.Column("conversation_id", sa.Text, primary_key=True),
        sa.Column("url", sa.Text, primary_key=True),
        sa.Column("blob_key", sa.Text, nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "provenance", pg.JSONB, nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_table("fetched_pages")
