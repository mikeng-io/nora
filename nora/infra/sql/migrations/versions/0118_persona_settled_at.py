"""persona.settled_at: when the account's own session last settled it.

design/unified-memory/45 §4 and 00 «Sequence» step 6. A turn's `told` line
(`written_by_session = 'tag'`) wakes the person's Persona session at once.
"Pending" is every such line written after the session last settled; a
session that settles with nothing to add (`persona_no_change`) inserts no
line, so the settle has to be stored on the account or the same line wakes
the session on every tick. NULL is a real state: never settled by a session.

Follows `0117_persona`. `uv run alembic heads` must print exactly ONE line.

Revision ID: 0118_persona_settled_at
Revises: 0117_persona
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0118_persona_settled_at"
down_revision = "0117_persona"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("persona", sa.Column("settled_at", sa.BigInteger, nullable=True))


def downgrade() -> None:
    op.drop_column("persona", "settled_at")
