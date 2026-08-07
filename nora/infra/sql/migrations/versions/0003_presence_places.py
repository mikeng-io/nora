"""presence_places — a circle's named landmarks (design/23 §3)

Third table in the one `nora` database. Static household names for positions
(«Home»), fetched from Life360's places endpoint by the collector and replaced
wholesale — a separate table from `presence_fixes` because a fix is where a
person WAS (R-31-guarded) and a place is somewhere the household chose to name
(design/23 §2 is the argument).

Hand-written like its siblings, though this one has no DESC index to protect —
the natural key `(circle_id, place_id)` is the primary key and every read is
"all places for this circle", which the PK prefix serves. The column list and
its ORDER match `nora.infra.sql.schema.PRESENCE_PLACES` exactly;
`tests/infra/test_schema_parity.py` fails on any drift.

Revision ID: 0003_presence_places
Revises: 0002_presence_fixes
Create Date: 2026-08-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_presence_places"
down_revision = "0002_presence_fixes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "presence_places",
        sa.Column("circle_id", sa.Text, primary_key=True),
        sa.Column("place_id", sa.Text, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("lat", sa.Float, nullable=False),
        sa.Column("lng", sa.Float, nullable=False),
        sa.Column("radius_m", sa.Float),
        sa.Column("updated_ms", sa.BigInteger, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("presence_places")
