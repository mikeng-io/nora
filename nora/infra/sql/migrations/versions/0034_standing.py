"""standing -- Nora's own position in one conversation, revised in place
(design/dreamer-agent-loop/26-standing.md).

One row per `(platform, conversation_id)`, and the composite primary key IS
S4's decision: every lock, queue and slot in this repo keys that way and the
`messages` table carries the platform column, so a `conversation_id`-only key
would be a join nobody can make. There is no separate id and no history table
-- the row is REVISED, and the raw material a lossy revision could be rebuilt
from is `tag_trace` (migration 0030), which is why retention had to ship first.

**`standing_body_fits_the_ceiling_check` is the CEILING, not the operating
cap** (26 sec. 4.0, operator decision 2026-08-24). `[standing] max_chars`
(1200 by default) is what the sink refuses at and the operator may move; 4000
here is the absolute bound no writer can pass, config included -- a backstop
against a config typo or a future writer nobody has thought of. The number is
written out rather than read from `nora.domain.standing`: a migration is a
frozen historical statement, and one that read a live constant would silently
change what it applied when the constant moved. `nora/infra/sql/schema.py`
reads the constant, so `tests/infra/test_schema_parity.py` is what catches a
change to one that did not reach the other.

Named for the same reason `note_title_fits_the_index_check` is: the cap exists
BECAUSE the field is injected -- on every turn, forever.

Revision ID: 0034_standing
Revises: 0033_reminder_event_kind
Create Date: 2026-08-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0034_standing"
down_revision = "0033_reminder_event_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "standing",
        sa.Column("platform", sa.Text, primary_key=True),
        sa.Column("conversation_id", sa.Text, primary_key=True),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("fold_count", sa.Integer, nullable=False,
                  server_default=sa.text("0")),
        sa.Column("updated_at", sa.BigInteger, nullable=False),
        sa.CheckConstraint(
            "length(btrim(body)) > 0 and length(body) <= 4000",
            name="standing_body_fits_the_ceiling_check",
        ),
        sa.CheckConstraint(
            "fold_count >= 0", name="standing_fold_count_is_not_negative_check",
        ),
    )


def downgrade() -> None:
    op.drop_table("standing")
