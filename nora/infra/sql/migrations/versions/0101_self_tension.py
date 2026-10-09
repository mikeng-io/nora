"""self_tension — where a lesson Nora would have learned pulls against the law.

Design/self-growth/40-lessons.md §4-§5, step L3; design/cognition-loop/55-self.md
§4. When the doctrine check (a separate instrument call) finds that a lesson would
loosen or contradict a LAW rule (DEC-291), the lesson is refused and one row is
written here: the rule quoted with its file and line, what the lesson would have
said, the evidence it cited, and the instrument's reason. OPERATOR-ONLY: no read
tool returns a row and no block renders one (a test pins both).

APPEND-ONLY, like `lesson_revision`: nothing is updated, so no
`nora_row_history` trigger; `recorded_at` is its system time
(`nora.infra.sql.history.APPEND_ONLY`).

Follows `0100_lessons`. `uv run alembic heads` must print exactly ONE line.

Revision ID: 0101_self_tension
Revises: 0100_lessons
Create Date: 2026-10-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0101_self_tension"
down_revision = "0100_lessons"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "self_tension",
        sa.Column("id", pg.UUID(as_uuid=False), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("unit", sa.Text, nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("doctrine_file", sa.Text, nullable=False),
        sa.Column("doctrine_line", sa.Integer, nullable=False),
        sa.Column("doctrine_text", sa.Text, nullable=False),
        sa.Column("would_have_said", sa.Text, nullable=False),
        sa.Column("citations", pg.ARRAY(sa.Text), nullable=False),
        sa.Column("why", sa.Text, nullable=False),
        sa.Column("written_at", sa.BigInteger, nullable=False),
        sa.Column("established_by", sa.Text, nullable=False),
        sa.Column("instruction_sha256", sa.Text),
        sa.Column("model", sa.Text),
        sa.Column("check_model", sa.Text),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.CheckConstraint("length(btrim(unit)) > 0", name="ck_self_tension_has_a_unit"),
        sa.CheckConstraint("source IN ('lesson')", name="ck_self_tension_source"),
        sa.CheckConstraint(
            "length(btrim(doctrine_text)) > 0", name="ck_self_tension_quotes_the_law",
        ),
        sa.CheckConstraint(
            "length(btrim(would_have_said)) > 0", name="ck_self_tension_says_what_it_would",
        ),
        sa.CheckConstraint(
            "length(btrim(established_by)) > 0", name="ck_self_tension_names_its_writer",
        ),
    )
    op.create_index("idx_self_tension_unit", "self_tension", ["unit", "written_at"])


def downgrade() -> None:
    op.drop_index("idx_self_tension_unit", table_name="self_tension")
    op.drop_table("self_tension")
