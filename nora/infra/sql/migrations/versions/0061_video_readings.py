"""video_readings -- a durable cache for question-less VIDEO readings.

`media_fetch` never cached a video: its reading depends on the chunk geometry
as well as the bytes, and caching it on the sha256 alone served a partial read
as a complete one (issue #117). So every look at a clip -- a Reel or Story by
URL, or a room clip with no ingest reading -- paid a full video read, 60-120 s
a window, on every ask.

This table keys the reading on EVERYTHING it depends on: the bytes, the
describer model, the chunk geometry and a digest of the prompt. It is not
`media_objects`, whose row promises the bytes are held: a clip read by URL has
no blob, and a key wider than a sha256 cannot sit in a table that handle
lookups and search read as one.

Revision ID: 0061_video_readings
Revises: 0060_message_edit
Create Date: 2026-09-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0061_video_readings"
down_revision = "0060_message_edit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_readings",
        sa.Column("sha256", sa.Text, nullable=False),
        sa.Column("model", sa.Text, nullable=False),
        sa.Column("chunk_seconds", sa.Integer, nullable=False),
        sa.Column("chunk_max", sa.Integer, nullable=False),
        sa.Column("prompt_digest", sa.Text, nullable=False),
        sa.Column("reading", sa.Text, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint(
            "sha256", "model", "chunk_seconds", "chunk_max", "prompt_digest",
        ),
    )


def downgrade() -> None:
    op.drop_table("video_readings")
