"""decision_log -- one row per ambient decision, so Nora's three deciders can
see each other's verdicts (issue #199, design/ambient-loop).

Nora has three paths that can take a room's floor and, until this table, no
shared state between them. The Relevance Gate persisted NOTHING durable --
metrics and trace attributes only. The proactive adaptor kept a log of its own
reminder rows. The determination log is written and read back into no prompt.
Measured consequence, 2026-09-10: participation answered a question at 16:43,
the tag turn one second later re-derived the whole answer and died, and the
proactive decider raised the same question in another room at 18:03.

APPEND-ONLY, AND `id` IS THE PRIMARY KEY. There is no update path in the store
and no surrogate sequence here: every decision is its own row, so a duplicate id
means a caller reused one, and Postgres saying so is better than a silently
replaced decision. The same shape `0035_turn_verdict` uses for the opposite
reason -- there the natural key forbids a second verdict per turn; here the
generated key makes a second row per decision impossible to confuse with the
first.

TWO INDEXES, NOT ONE. `(conversation_id, ts DESC, id DESC)` serves the read both
prompt renders make -- one room, newest first, inside a 24h window.
`(workspace, ts DESC, id DESC)` serves the workspace-keyed read, which names no
conversation and would otherwise scan. The `id DESC` column is the tie-break:
two decisions in one millisecond are reachable (issue #199's own incident is one
second apart), and without it Postgres answers whatever the scan reached first
while the in-memory double answers hash order -- two implementations rendering
different blocks from identical rows, which a contract suite cannot see.

THE NUMBERS AND THE VOCABULARIES ARE WRITTEN OUT rather than read from
`nora.domain.decision_log`, following `0035_turn_verdict`: a migration is a
frozen historical statement, and one reading a live constant silently changes
what it applied when the constant moves. `nora/infra/sql/schema.py` reads the
constants, so `tests/infra/test_schema_parity.py` catches a change to one that
did not reach the other.

`ts DESC` is authored by hand for `idx_messages_conv_ts`'s reason: autogenerate
cannot see per-column index ordering and reports permanent phantom drift on it.

Revision ID: 0053_decision_log
Revises: 0052_media_comprehension
Create Date: 2026-09-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0053_decision_log"
down_revision = "0052_media_comprehension"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "decision_log",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("writer", sa.Text, nullable=False),
        sa.Column("platform", sa.Text, nullable=False),
        sa.Column("conversation_id", sa.Text, nullable=False),
        sa.Column("workspace", sa.Text, nullable=False, server_default=""),
        sa.Column("note_id", sa.Text, nullable=True),
        sa.Column("verdict", sa.Text, nullable=False),
        sa.Column("reason", sa.Text, nullable=False, server_default=""),
        sa.Column("ts", sa.BigInteger, nullable=False),
        sa.CheckConstraint(
            "writer in ('participation', 'proactive_decide', "
            "'proactive_act', 'tag')",
            name="decision_log_writer_is_known_check",
        ),
        sa.CheckConstraint(
            "verdict in ('spoke', 'silent', 'remind', "
            "'decline', 'replied', 'failed')",
            name="decision_log_verdict_is_known_check",
        ),
        sa.CheckConstraint(
            "length(reason) <= 80",
            name="decision_log_reason_fits_the_block_check",
        ),
    )
    op.create_index(
        "idx_decision_log_conv_ts", "decision_log",
        ["conversation_id", sa.text("ts DESC"), sa.text("id DESC")],
    )
    op.create_index(
        "idx_decision_log_workspace_ts", "decision_log",
        ["workspace", sa.text("ts DESC"), sa.text("id DESC")],
    )


def downgrade() -> None:
    op.drop_index("idx_decision_log_workspace_ts", table_name="decision_log")
    op.drop_index("idx_decision_log_conv_ts", table_name="decision_log")
    op.drop_table("decision_log")
