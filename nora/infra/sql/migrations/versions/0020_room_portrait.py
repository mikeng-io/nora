"""room portrait: one row per scope, UPSERT (Dreamer Plan 3).

No watermark row — a portrait has no evidence layer of its own; it is entirely
re-derived from Plan 1/2's already-durable state on every trigger. Table is
empty at every deployment this migration reaches, so no index tuning needed
beyond the primary key.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0020_room_portrait"
down_revision = "0019_exchange_patterns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "room_portrait",
        sa.Column("scope_id", sa.Text, primary_key=True),
        sa.Column("narrative", sa.Text, nullable=False),
        sa.Column(
            "lexeme_basis", pg.ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "exchange_basis", pg.ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'"),
        ),
        sa.Column("synthesized_at", sa.BigInteger, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("room_portrait")
