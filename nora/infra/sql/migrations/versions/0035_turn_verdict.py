"""turn_verdict -- the debrief's per-TURN judgement, written once and never
rewritten (design/dreamer-agent-loop/26-standing.md sec. 3).

The opposite cardinality to `standing` (migration 0034), and the split is
structural. It was found by a bug: the first draft put these fields on the
per-conversation row, so the re-derivation trigger read a `grounded` flag the
NEXT TURN had already overwritten. A per-turn fact on a row revised every turn
is a fact with a lifetime of one turn.

**`tag_id` is the PRIMARY KEY and that IS the never-rewritten guarantee.**
Exactly one verdict exists per turn by definition, so a surrogate id would make
"two verdicts for one turn" representable. With the natural key a second write
is a duplicate-key error from Postgres rather than a rule a future writer has
to have read -- and `SqlTurnVerdictStore.save` is a plain INSERT with no
ON CONFLICT, so there is no update path to take.

The numbers in the CHECKs are written out rather than read from
`nora.domain.turn_verdict`: a migration is a frozen historical statement, and
one reading a live constant silently changes what it applied when the constant
moves. `nora/infra/sql/schema.py` reads the constants, so
`tests/infra/test_schema_parity.py` catches a change to one that did not reach
the other.

Revision ID: 0035_turn_verdict
Revises: 0034_standing
Create Date: 2026-08-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0035_turn_verdict"
down_revision = "0034_standing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "turn_verdict",
        sa.Column("tag_id", sa.Text, primary_key=True),
        sa.Column("conversation_id", sa.Text, nullable=False),
        sa.Column("grounded", sa.Boolean, nullable=False),
        sa.Column("unsupported", pg.ARRAY(sa.Text), nullable=False,
                  server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.BigInteger, nullable=False),
        sa.CheckConstraint(
            "not (grounded and cardinality(unsupported) > 0)",
            name="turn_verdict_grounded_has_no_unsupported_check",
        ),
        sa.CheckConstraint(
            "cardinality(unsupported) <= 10",
            name="turn_verdict_unsupported_fits_the_list_check",
        ),
    )
    # Partial on the minority side: the re-derivation trigger only ever asks
    # about UNGROUNDED turns, so a grounded row has no reason to be indexed.
    op.create_index(
        "idx_turn_verdict_ungrounded", "turn_verdict",
        ["conversation_id", "created_at"],
        postgresql_where=sa.text("grounded = false"),
    )


def downgrade() -> None:
    op.drop_index("idx_turn_verdict_ungrounded", table_name="turn_verdict")
    op.drop_table("turn_verdict")
