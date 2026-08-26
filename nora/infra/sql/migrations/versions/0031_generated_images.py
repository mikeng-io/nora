"""generated_images — Nora's own generated-image library (Miles 2026-08-26:
"image generated -> saved to rustfs -> send as attachment" + "if Nora needs
to look it up later, we need a title, description, and the image generation
prompt saved properly inside the database").

One row per successful `generate_image` call. `sha256` is the content
address of the bytes in the SAME `blob_store` (rustfs) the inbound media
pipeline uses — a generated image is custody-first, never a disposable
attachment. The prompt/title/description are what make a later lookup
meaningful: Nora searches this table by text and pulls the bytes by sha256,
so she can resend or reuse an image days later without regenerating it
(no quota cost, no new bytes).

`created_at` is ms-epoch, matching `messages.ts` (this table's sibling), so
a join on time is a plain integer comparison.

HAND-WRITTEN for the same reason 0028/0029/0030 are: the parity gate
(`tests/infra/test_schema_parity.py`) is what catches drift between this
file and `nora.infra.sql.schema.GENERATED_IMAGES`, and this file's column
list and order MUST match that table exactly.

Revision ID: 0031_generated_images
Revises: 0030_tag_trace
Create Date: 2026-08-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0031_generated_images"
down_revision = "0030_tag_trace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "generated_images",
        sa.Column("sha256", sa.Text, primary_key=True),
        sa.Column("prompt", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("model", sa.Text, nullable=False),
        sa.Column("aspect_ratio", sa.Text),
        sa.Column("media_type", sa.Text, nullable=False),
        sa.Column("size", sa.BigInteger, nullable=False),
        sa.Column("platform", sa.Text, nullable=False),
        sa.Column("conversation_id", sa.Text, nullable=False),
        sa.Column("created_at_ms", sa.BigInteger, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("generated_images")
