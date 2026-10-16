"""consolidation_watermark.last_message_id: INTEGER to BIGINT.

A person's Character session keeps a second cursor on this table (the row
`character-person:social`, `nora.infra.sql.character.SqlCharacterEvidence
.social_cursor`): the `recorded_at` of the last social item it was handed, in
microseconds since the epoch, about 1.8e15, past INTEGER's 2,147,483,647.
`social_item` has no serial id to key it on (its id is a uuid), and the
microsecond is what keeps a read from splitting items recorded together
(design/cognition-loop/22 §6). Every value an INTEGER held is unchanged.

Follows `0113_memory_social_location`. `uv run alembic heads` must print exactly ONE
line.

Revision ID: 0114_watermark_bigint
Revises: 0113_memory_social_location
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0114_watermark_bigint"
down_revision = "0113_memory_social_location"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "consolidation_watermark", "last_message_id",
        type_=sa.BigInteger(), existing_type=sa.Integer(), existing_nullable=False,
        existing_server_default=sa.text("0"),
    )


def downgrade() -> None:
    # Refuses (out of range) while a social cursor row holds a microsecond
    # value: delete the `%:social` rows first, and those sessions re-hand
    # every item from the start.
    op.alter_column(
        "consolidation_watermark", "last_message_id",
        type_=sa.Integer(), existing_type=sa.BigInteger(), existing_nullable=False,
        existing_server_default=sa.text("0"),
    )
