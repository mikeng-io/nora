"""presence_occupancy + presence_place_rhythm — Tier 3 rhythm/anomaly
(design/27 §4, issue #6, DEC-117).

Revision ID: 0017_presence_rhythm
Revises: 0016_geocode_cache
Create Date: 2026-08-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017_presence_rhythm"
down_revision = "0016_geocode_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "presence_occupancy",
        sa.Column("id", sa.Integer, sa.Identity(always=False), primary_key=True),
        sa.Column("subject_id", sa.Text, nullable=False),
        sa.Column("place_id", sa.Integer, nullable=False),
        sa.Column("hour_of_week", sa.Integer, nullable=False),
        sa.Column("dwell_ms", sa.BigInteger, nullable=False, server_default=sa.text("0")),
        sa.Column("updated_ts", sa.BigInteger, nullable=False),
        sa.UniqueConstraint(
            "subject_id", "place_id", "hour_of_week", name="uq_occupancy_bin"
        ),
        sa.CheckConstraint(
            "hour_of_week >= 0 AND hour_of_week < 168", name="ck_occupancy_how"
        ),
    )
    op.create_table(
        "presence_place_rhythm",
        sa.Column("subject_id", sa.Text, primary_key=True),
        sa.Column("place_id", sa.Integer, primary_key=True),
        sa.Column("mean_arrival_minute", sa.Float, nullable=True),
        sa.Column("mean_departure_minute", sa.Float, nullable=True),
        sa.Column("duration_median_ms", sa.BigInteger, nullable=True),
        sa.Column("duration_mad_ms", sa.BigInteger, nullable=True),
        sa.Column("sample_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("updated_ts", sa.BigInteger, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("presence_place_rhythm")
    op.drop_table("presence_occupancy")
