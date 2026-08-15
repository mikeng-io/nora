"""presence_fixes gains speed_kmh — Life360's own reported ground speed,
converted from its wire m/s at parse time (`nora.infra.life360.members`).
Nullable, matching `accuracy_m`/`battery`: Life360 omits it for a still
member, and its own `-1` "unknown speed" sentinel is already collapsed to
`None` before a fix ever reaches this table, so every value stored here is a
real measured speed.

Revision ID: 0011_presence_speed
Revises: 0010_tool_call_log
Create Date: 2026-08-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_presence_speed"
down_revision = "0010_tool_call_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("presence_fixes", sa.Column("speed_kmh", sa.Float))


def downgrade() -> None:
    op.drop_column("presence_fixes", "speed_kmh")
