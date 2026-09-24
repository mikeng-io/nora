"""note_merge_decision -- what the note-merge pass considered folding, one row
per (survivor, duplicate) pair (design/dreamer-agent-loop/41 §7.8, DEC-202).

In `shadow` mode this table is the pass's whole output: the groups it would
fold, with both titles and the model's reason, for the operator to label
before `on`. In `on` it is the audit of each fold and each refusal.

NOT `decision_log`. That table's rows render into the ambient deciders'
prompts, its `reason` is an 80-character code label, and it refuses note titles
and model text by its own rule. This one holds both and no prompt reads it.

THE VOCABULARIES ARE WRITTEN OUT rather than read from `nora.domain.notes`,
following `0053_decision_log`: a migration is a frozen historical statement.
`nora/infra/sql/schema.py` reads the constants, and
`tests/infra/test_schema_parity.py` catches a change to one that did not reach
the other.

Revision ID: 0063_note_merge_decision
Revises: 0062_standing_revision_source
Create Date: 2026-09-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0063_note_merge_decision"
down_revision = "0062_standing_revision_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "note_merge_decision",
        sa.Column("id", pg.UUID(as_uuid=False), primary_key=True),
        sa.Column("unit", sa.Text, nullable=False),
        sa.Column("mode", sa.Text, nullable=False),
        sa.Column("outcome", sa.Text, nullable=False),
        sa.Column(
            "survivor_id", pg.UUID(as_uuid=False),
            sa.ForeignKey("note.id", name="note_merge_decision_survivor_fkey"),
            nullable=False,
        ),
        sa.Column(
            "duplicate_id", pg.UUID(as_uuid=False),
            sa.ForeignKey("note.id", name="note_merge_decision_duplicate_fkey"),
            nullable=False,
        ),
        sa.Column("survivor_title", sa.Text, nullable=False),
        sa.Column("duplicate_title", sa.Text, nullable=False),
        sa.Column("reason", sa.Text, nullable=False, server_default=""),
        sa.Column("established_by", sa.Text, nullable=False),
        sa.Column("ts", sa.BigInteger, nullable=False),
        sa.CheckConstraint(
            "mode in ('shadow', 'on')",
            name="note_merge_decision_mode_is_known_check",
        ),
        sa.CheckConstraint(
            "outcome in ('would_merge', 'merged', 'stale', 'too_long', "
            "'not_open', 'overlap', 'cross_room', 'not_changed')",
            name="note_merge_decision_outcome_is_known_check",
        ),
        sa.CheckConstraint(
            "length(reason) <= 500",
            name="note_merge_decision_reason_is_a_sentence_check",
        ),
    )
    op.create_index(
        "idx_note_merge_decision_unit_ts", "note_merge_decision",
        ["unit", "ts"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_note_merge_decision_unit_ts", table_name="note_merge_decision",
    )
    op.drop_table("note_merge_decision")
