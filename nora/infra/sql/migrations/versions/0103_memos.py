"""memo, memo_revision — what a unit settled and keeps (Register · memo).

DEC-306, design/cognition-loop/40-register.md §2 and 12-schemas.md §3. The
lesson's shape (`0100_lessons`): two APPEND-ONLY tables, a `memo` is an identity
in one unit with the room it was first kept in, and every change (a revision, a
withdrawal) is one more `memo_revision`, so the head is the highest `seq`.
Nothing is updated, so no `nora_row_history` trigger:
`nora.infra.sql.history.APPEND_ONLY` names `recorded_at` as each table's system
time. No foreign key between them, for the reason `0084_documents` gives.

Follows `0102_event_owns_no_reminder`. `uv run alembic heads` must print exactly
ONE line.

Revision ID: 0103_memos
Revises: 0102_event_owns_no_reminder
Create Date: 2026-10-09
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0103_memos"
down_revision = "0102_event_owns_no_reminder"
branch_labels = None
depends_on = None


def _recorded_at() -> sa.Column[Any]:
    return sa.Column(
        "recorded_at", sa.DateTime(timezone=True), nullable=False,
        server_default=sa.text("clock_timestamp()"),
    )


def upgrade() -> None:
    op.create_table(
        "memo",
        sa.Column("id", pg.UUID(as_uuid=False), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("unit", sa.Text, nullable=False),
        sa.Column("platform", sa.Text, nullable=False),
        sa.Column("conversation_id", sa.Text, nullable=False),
        sa.Column("created_at", sa.BigInteger, nullable=False),
        _recorded_at(),
        sa.CheckConstraint("length(btrim(unit)) > 0", name="ck_memo_has_a_unit"),
    )
    op.create_index("idx_memo_unit", "memo", ["unit"])
    op.create_table(
        "memo_revision",
        sa.Column("id", pg.UUID(as_uuid=False), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("memo_id", pg.UUID(as_uuid=False), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("citations", pg.ARRAY(sa.Text), nullable=False),
        sa.Column("mode", sa.Text, nullable=False),
        sa.Column("state", sa.Text, nullable=False),
        sa.Column("written_at", sa.BigInteger, nullable=False),
        sa.Column("established_by", sa.Text, nullable=False),
        sa.Column("model", sa.Text),
        _recorded_at(),
        sa.CheckConstraint("seq >= 1", name="ck_memo_revision_seq"),
        sa.CheckConstraint("length(btrim(title)) > 0", name="ck_memo_revision_has_a_title"),
        sa.CheckConstraint("length(title) <= 200", name="ck_memo_revision_title_ceiling"),
        sa.CheckConstraint("length(btrim(body)) > 0", name="ck_memo_revision_has_a_body"),
        sa.CheckConstraint("length(body) <= 4000", name="ck_memo_revision_body_ceiling"),
        sa.CheckConstraint(
            "cardinality(citations) >= 1", name="ck_memo_revision_cites_something",
        ),
        sa.CheckConstraint(
            "mode IN ('told', 'deduced', 'abduced')", name="ck_memo_revision_mode",
        ),
        sa.CheckConstraint("state IN ('kept', 'withdrawn')", name="ck_memo_revision_state"),
        sa.CheckConstraint(
            "length(btrim(established_by)) > 0", name="ck_memo_revision_names_its_writer",
        ),
    )
    op.create_index(
        "uq_memo_revision_seq", "memo_revision", ["memo_id", "seq"], unique=True,
    )


def downgrade() -> None:
    # Going back down forgets every memo; notes and events are untouched.
    op.drop_index("uq_memo_revision_seq", table_name="memo_revision")
    op.drop_table("memo_revision")
    op.drop_index("idx_memo_unit", table_name="memo")
    op.drop_table("memo")
