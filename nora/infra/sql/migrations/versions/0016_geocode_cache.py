"""geocode_cache — coordinate to Google-resolved address, lazy and persistent
(follow-up to 0015_presence_analytics; see nora/infra/sql/schema.py's own
comment for the full ownership/boundary rationale).

Revision ID: 0016_geocode_cache
Revises: 0015_presence_analytics
Create Date: 2026-08-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016_geocode_cache"
down_revision = "0015_presence_analytics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "geocode_cache",
        sa.Column("lat_rounded", sa.Float, primary_key=True),
        sa.Column("lng_rounded", sa.Float, primary_key=True),
        sa.Column("address", sa.Text, nullable=False),
        sa.Column(
            "resolved_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("geocode_cache")
