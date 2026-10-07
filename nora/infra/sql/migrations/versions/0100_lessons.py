"""lesson, lesson_revision — practice induced from the outcomes of her acts, per unit.

Design/self-growth/40-lessons.md, step L1. Two APPEND-ONLY tables: a `lesson` is an
identity in one unit, and every change to what it says or whether it is in force is
one more `lesson_revision` (reinforce, reword, retire, an operator's rollback), so
the head is the highest `seq`. Nothing is updated, so no `nora_row_history` trigger:
`nora.infra.sql.history.APPEND_ONLY` names `recorded_at` as each table's system
time. No foreign key between them, for the reason `0084_documents` gives.

`retired_by = 'operator'` carries `relearn_after`, the newest Record id the lesson
rested on or that existed when the operator retired it: a new lesson resting only
on evidence at or below it is a re-learn and is refused.

Follows `0099_rollup_response_follows`. `uv run alembic heads` must print exactly
ONE line.

Revision ID: 0100_lessons
Revises: 0099_rollup_response_follows
Create Date: 2026-10-07
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0100_lessons"
down_revision = "0099_rollup_response_follows"
branch_labels = None
depends_on = None


def _recorded_at() -> sa.Column[Any]:
    return sa.Column(
        "recorded_at", sa.DateTime(timezone=True), nullable=False,
        server_default=sa.text("clock_timestamp()"),
    )


def upgrade() -> None:
    op.create_table(
        "lesson",
        sa.Column("id", pg.UUID(as_uuid=False), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("unit", sa.Text, nullable=False),
        sa.Column("created_at", sa.BigInteger, nullable=False),
        _recorded_at(),
        sa.CheckConstraint("length(btrim(unit)) > 0", name="ck_lesson_has_a_unit"),
    )
    op.create_index("idx_lesson_unit", "lesson", ["unit"])
    op.create_table(
        "lesson_revision",
        sa.Column("id", pg.UUID(as_uuid=False), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("lesson_id", pg.UUID(as_uuid=False), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("citations", pg.ARRAY(sa.Text), nullable=False),
        sa.Column("state", sa.Text, nullable=False),
        sa.Column("retired_by", sa.Text),
        sa.Column("relearn_after", sa.BigInteger),
        sa.Column("note", sa.Text),
        sa.Column("written_at", sa.BigInteger, nullable=False),
        sa.Column("established_by", sa.Text, nullable=False),
        sa.Column("instruction_sha256", sa.Text),
        sa.Column("model", sa.Text),
        _recorded_at(),
        sa.CheckConstraint("seq >= 1", name="ck_lesson_revision_seq"),
        sa.CheckConstraint("length(btrim(body)) > 0", name="ck_lesson_revision_has_a_body"),
        sa.CheckConstraint("length(body) <= 2000", name="ck_lesson_revision_body_ceiling"),
        sa.CheckConstraint(
            "cardinality(citations) >= 1", name="ck_lesson_revision_cites_something",
        ),
        sa.CheckConstraint("state IN ('active', 'retired')", name="ck_lesson_revision_state"),
        sa.CheckConstraint(
            "(state = 'retired') = (retired_by IS NOT NULL)",
            name="ck_lesson_revision_retired_by",
        ),
        sa.CheckConstraint(
            "retired_by IS NULL OR retired_by IN ('session', 'operator')",
            name="ck_lesson_revision_retired_by_who",
        ),
        sa.CheckConstraint(
            "(retired_by = 'operator') = (relearn_after IS NOT NULL)",
            name="ck_lesson_revision_relearn_after",
        ),
        sa.CheckConstraint(
            "length(btrim(established_by)) > 0", name="ck_lesson_revision_names_its_writer",
        ),
        sa.CheckConstraint(
            "instruction_sha256 IS NULL OR instruction_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_lesson_revision_instruction_sha256",
        ),
    )
    op.create_index(
        "uq_lesson_revision_seq", "lesson_revision", ["lesson_id", "seq"], unique=True,
    )


def downgrade() -> None:
    # Going back down forgets every lesson; the doctrine and Character are untouched.
    op.drop_index("uq_lesson_revision_seq", table_name="lesson_revision")
    op.drop_table("lesson_revision")
    op.drop_index("idx_lesson_unit", table_name="lesson")
    op.drop_table("lesson")
