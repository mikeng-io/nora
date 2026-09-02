"""presence_places.address — the address column the SavedPlace parser has
carried since 2026-08-31 but no store layer persisted (audit blocker:
`saved_places` promised «我返工嗰度個地址係咩» from an address that was
dropped on write and never read back).

Revision ID: 0045_places_address
Revises: 0044_embedding
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0045_places_address"
down_revision = "0044_embedding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("presence_places", sa.Column("address", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("presence_places", "address")
