"""persona_dispute: a dispute of a Persona line is a Record item.

design/unified-memory/35 §2, 00 «Sequence» step 6. Anyone may dispute any
line; the dispute names the line when one matches, who disputed, the claim
as they put it and the message they said it in. Append-only: what was
disputed, when and by whom is history the weighing session reads, never
updated in place. What the dispute DID to the line (hid it, marked it
`disputed`) is a new `persona_line` row in the same transaction
(`SqlPersonaStore.record_dispute`), not a column here. No foreign keys, for
the reason `0117_persona` gives; a line is named by id and a line row is
never deleted.

Follows `0120_persona_settled_at`. `uv run alembic heads` must print exactly
ONE line.

Revision ID: 0121_persona_dispute
Revises: 0120_persona_settled_at
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0121_persona_dispute"
down_revision = "0120_persona_settled_at"
branch_labels = None
depends_on = None


def _recorded_at() -> sa.Column[Any]:
    return sa.Column(
        "recorded_at", sa.DateTime(timezone=True), nullable=False,
        server_default=sa.text("clock_timestamp()"),
    )


def upgrade() -> None:
    op.create_table(
        "persona_dispute",
        sa.Column("id", pg.UUID(as_uuid=False), primary_key=True),
        sa.Column("persona_key", sa.Text, nullable=False),
        # The line the claim matched, or NULL when none did unambiguously:
        # the row still wakes the account's session, which weighs it there.
        sa.Column("line_id", pg.UUID(as_uuid=False)),
        sa.Column("disputed_by", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("claim", sa.Text, nullable=False),
        sa.Column("message_id", sa.BigInteger, nullable=False),
        sa.Column("unit", sa.Text, nullable=False),
        # When the dispute was recorded (epoch ms), the clock
        # `persona.settled_at` is compared against for «pending».
        sa.Column("written_at", sa.BigInteger, nullable=False),
        _recorded_at(),
        sa.CheckConstraint("kind IN ('dispute', 'forget')", name="ck_persona_dispute_kind"),
        sa.CheckConstraint(
            "length(btrim(claim)) > 0 AND length(claim) <= 300",
            name="ck_persona_dispute_claim",
        ),
        sa.CheckConstraint(
            "length(btrim(disputed_by)) > 0", name="ck_persona_dispute_who",
        ),
        sa.CheckConstraint(
            "length(btrim(persona_key)) > 0", name="ck_persona_dispute_has_a_key",
        ),
        sa.CheckConstraint(
            "length(btrim(unit)) > 0", name="ck_persona_dispute_has_a_unit",
        ),
    )
    op.create_index(
        "idx_persona_dispute_key", "persona_dispute", ["persona_key", "written_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_persona_dispute_key", table_name="persona_dispute")
    op.drop_table("persona_dispute")
