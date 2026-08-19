"""presence_trips + presence_corridors + presence_corridor_speed — Tier 5
routes (design/27 §4, issue #6).

Revision ID: 0019_presence_routes
Revises: 0018_presence_rhythm
Create Date: 2026-08-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0019_presence_routes"
down_revision = "0018_presence_rhythm"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "presence_trips",
        sa.Column("id", sa.Integer, sa.Identity(always=False), primary_key=True),
        sa.Column("subject_id", sa.Text, nullable=False),
        sa.Column("person_id", sa.Text, nullable=False),
        sa.Column("circle_id", sa.Text, nullable=False),
        sa.Column("started_ts", sa.BigInteger, nullable=False),
        sa.Column("ended_ts", sa.BigInteger, nullable=False),
        sa.Column("distance_m", sa.Float, nullable=False),
        sa.Column("avg_speed_kmh", sa.Float, nullable=False),
        sa.Column("fix_count", sa.Integer, nullable=False),
        sa.Column("corridor_id", sa.Integer, nullable=True),
    )
    op.create_index(
        "idx_presence_trips_subject_started", "presence_trips",
        ["subject_id", sa.text("started_ts DESC")],
    )
    op.create_table(
        "presence_corridors",
        sa.Column("id", sa.Integer, sa.Identity(always=False), primary_key=True),
        sa.Column("subject_id", sa.Text, nullable=False),
        sa.Column("sequence", pg.JSONB, nullable=False),
        sa.Column("sample_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("first_seen_ts", sa.BigInteger, nullable=False),
        sa.Column("last_seen_ts", sa.BigInteger, nullable=False),
    )
    op.create_index(
        "idx_presence_corridors_subject", "presence_corridors", ["subject_id"],
    )
    op.create_table(
        "presence_corridor_speed",
        sa.Column("subject_id", sa.Text, primary_key=True),
        sa.Column("corridor_id", sa.Integer, primary_key=True),
        sa.Column("hour_of_week", sa.Integer, primary_key=True),
        sa.Column("p50_kmh", sa.Float, nullable=False),
        sa.Column("p90_kmh", sa.Float, nullable=False),
        sa.Column("sample_count", sa.Integer, nullable=False),
        sa.Column("updated_ts", sa.BigInteger, nullable=False),
        sa.CheckConstraint(
            "hour_of_week >= 0 AND hour_of_week < 168",
            name="ck_corridor_speed_how",
        ),
    )


def downgrade() -> None:
    op.drop_table("presence_corridor_speed")
    op.drop_table("presence_corridors")
    op.drop_index("idx_presence_trips_subject_started", table_name="presence_trips")
    op.drop_table("presence_trips")
