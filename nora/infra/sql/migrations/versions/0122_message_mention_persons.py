"""messages.mention_persons_json: the @-mentioned accounts, resolved to persons.

design/unified-memory/25 §2 («mentions» edges: message -> person, from
resolved handles only) and 45 §4 (a Persona session reads what others say
ABOUT its subject). `mentions_json` holds the raw platform account ids; the
memory-index projector is pure SQL and cannot resolve them against
`identity.toml`, so — as `messages.person_id` already is for the sender —
the resolved person ids are stored on the row at ingest (and per edit), and
the projector draws `mentions` edges from them. NULL is «unknown»: a row
stored before this column or with no resolver wired, and an edit whose
platform could not carry mentions (`_edit_mentions_json`'s rule); the
projector reads an edit's value when it has one, else the row's.

Follows `0121_persona_dispute`. `uv run alembic heads` must print exactly
ONE line.

Revision ID: 0122_message_mention_persons
Revises: 0121_persona_dispute
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0122_message_mention_persons"
down_revision = "0121_persona_dispute"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("mention_persons_json", sa.Text, nullable=True))
    op.add_column("message_edit", sa.Column("mention_persons_json", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("message_edit", "mention_persons_json")
    op.drop_column("messages", "mention_persons_json")
