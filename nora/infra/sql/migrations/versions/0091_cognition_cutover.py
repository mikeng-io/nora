"""The Cognition cut-over: sessions write live, so the shadow tables go.

`cognition_would_write` and `cognition_remind_mirror` held what a shadow session
would have written; a session now commits through the tiers' own stores, so
nothing writes either and both are dropped (shadow-week evidence, not tier data).
`cognition_wake.mode` and `cognition_session.mode` become `'live'`, rows
included: a unit's watermark is its newest wake IN A MODE, so the shadow rows
are relabelled rather than left behind, or the first live wake would read no
watermark and claim every stimulus ever recorded. A `standing_revision` written
by a session says `source = 'cognition'`.

Revision ID: 0091_cognition_cutover
Revises: 0090_cognition_outcome_tokens
Create Date: 2026-10-03
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0091_cognition_cutover"
down_revision = "0090_cognition_outcome_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("cognition_would_write")
    op.drop_table("cognition_remind_mirror")
    for table, name in (
        ("cognition_wake", "ck_cognition_wake_mode"),
        ("cognition_session", "ck_cognition_session_mode"),
    ):
        op.drop_constraint(name, table, type_="check")
        op.execute(f"UPDATE {table} SET mode = 'live'")  # noqa: S608 -- table names are literals above
        op.create_check_constraint(name, table, "mode IN ('live')")
    op.drop_constraint(
        "standing_revision_source_is_fold_or_operator_check", "standing_revision",
        type_="check",
    )
    op.create_check_constraint(
        "standing_revision_source_check", "standing_revision",
        "source IN ('fold', 'operator', 'cognition')",
    )


def _recorded_at() -> sa.Column[Any]:
    return sa.Column(
        "recorded_at", sa.DateTime(timezone=True), nullable=False,
        server_default=sa.func.clock_timestamp(),
    )


def downgrade() -> None:
    """The two tables come back empty, in 0078/0079's shape, so the downgrades
    below this one find what they drop; the rows dropped on the way up are gone."""
    op.drop_constraint("standing_revision_source_check", "standing_revision", type_="check")
    op.create_check_constraint(
        "standing_revision_source_is_fold_or_operator_check", "standing_revision",
        "source IN ('fold', 'operator')",
    )
    for table, name in (
        ("cognition_wake", "ck_cognition_wake_mode"),
        ("cognition_session", "ck_cognition_session_mode"),
    ):
        op.drop_constraint(name, table, type_="check")
        op.execute(f"UPDATE {table} SET mode = 'shadow'")  # noqa: S608 -- literal table names
        op.create_check_constraint(name, table, "mode IN ('shadow')")
    op.create_table(
        "cognition_would_write",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column("session_id", sa.BigInteger, nullable=True),
        sa.Column("unit", sa.Text, nullable=False),
        sa.Column("tier", sa.Text, nullable=False),
        sa.Column("verb", sa.Text, nullable=False),
        sa.Column("platform", sa.Text, nullable=False),
        sa.Column("conversation_id", sa.Text, nullable=False),
        sa.Column("args", pg.JSONB, nullable=False),
        sa.Column("outcome", sa.Text, nullable=False),
        sa.Column("refusal_reason", sa.Text, nullable=True),
        sa.Column("refusal_detail", sa.Text, nullable=True),
        sa.Column("candidate", pg.JSONB, nullable=True),
        sa.Column("base_revision_id", sa.Text, nullable=True),
        _recorded_at(),
        sa.CheckConstraint(
            "tier IN ('note', 'event', 'standing')", name="ck_cognition_would_write_tier",
        ),
        sa.CheckConstraint(
            "outcome IN ('would_commit', 'refused')", name="ck_cognition_would_write_outcome",
        ),
        sa.CheckConstraint(
            "(outcome = 'would_commit' AND candidate IS NOT NULL) "
            "OR (outcome = 'refused' AND refusal_reason IS NOT NULL)",
            name="ck_cognition_would_write_outcome_shape",
        ),
    )
    op.create_index(
        "idx_cognition_would_write_session", "cognition_would_write", ["session_id"],
    )
    op.create_index(
        "idx_cognition_would_write_unit_recorded",
        "cognition_would_write", ["unit", "recorded_at"],
    )
    op.create_foreign_key(
        "fk_cognition_would_write_session_id",
        "cognition_would_write", "cognition_session", ["session_id"], ["id"],
    )
    op.create_table(
        "cognition_remind_mirror",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column(
            "session_id", sa.BigInteger, sa.ForeignKey("cognition_session.id"), nullable=True,
        ),
        sa.Column("unit", sa.Text, nullable=False),
        sa.Column("verb", sa.Text, nullable=False),
        sa.Column("reminder_ref", sa.Text, nullable=True),
        sa.Column("platform", sa.Text, nullable=False),
        sa.Column("conversation_id", sa.Text, nullable=False),
        sa.Column("intent", sa.Text, nullable=True),
        sa.Column("subject_rows", pg.JSONB, nullable=False),
        sa.Column("at_ms", sa.BigInteger, nullable=True),
        sa.Column("why", sa.Text, nullable=True),
        sa.Column("outcome", sa.Text, nullable=False),
        sa.Column("refusal_reason", sa.Text, nullable=True),
        sa.Column("refusal_detail", sa.Text, nullable=True),
        _recorded_at(),
        sa.CheckConstraint(
            "verb IN ('remind', 'remind_move', 'remind_cancel')",
            name="ck_cognition_remind_mirror_verb",
        ),
        sa.CheckConstraint(
            "outcome IN ('recorded', 'refused')", name="ck_cognition_remind_mirror_outcome",
        ),
        sa.CheckConstraint(
            "(outcome = 'recorded') OR (refusal_reason IS NOT NULL)",
            name="ck_cognition_remind_mirror_outcome_shape",
        ),
    )
    op.create_index(
        "idx_cognition_remind_mirror_unit_ref",
        "cognition_remind_mirror", ["unit", "reminder_ref"],
    )
