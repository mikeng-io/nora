"""persona, persona_line: one account per entity, kept as typed lines.

design/unified-memory/45 §1-§3, 00 «Sequence» step 5. `persona` is the
account and changes in place (`revision`, the compare-and-set a write is
judged against), so it gets `nora_row_history` (0068's function).
`persona_line` is APPEND-ONLY: a change inserts a row whose `supersedes`
names the one it replaces, and `uq_persona_line_supersedes` lets a line be
superseded once. The CHECKs carry what a row can say on its own (a kind
belongs to its part, a mode is allowed in its part, a told line names who
told it, a hypothesis carries its prediction, observable and window); the
rules that need the evidence or the other lines are
`nora.domain.persona.plan_write`'s, run inside the write's transaction. No
foreign key between the two, for the reason `0084_documents` gives. Nothing
renders these rows before the switch (step 8).

Follows `0115_watermark_bigint`. `uv run alembic heads` must print exactly
ONE line.

Revision ID: 0116_persona
Revises: 0115_watermark_bigint
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0116_persona"
down_revision = "0115_watermark_bigint"
branch_labels = None
depends_on = None


def _recorded_at() -> sa.Column[Any]:
    return sa.Column(
        "recorded_at", sa.DateTime(timezone=True), nullable=False,
        server_default=sa.text("clock_timestamp()"),
    )


def upgrade() -> None:
    op.create_table(
        "persona",
        sa.Column("key", sa.Text, primary_key=True),
        sa.Column("entity_kind", sa.Text, nullable=False),
        sa.Column("unit", sa.Text, nullable=False),
        sa.Column("revision", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("updated_at", sa.BigInteger, nullable=False),
        sa.CheckConstraint("length(btrim(key)) > 0", name="ck_persona_has_a_key"),
        sa.CheckConstraint("length(btrim(unit)) > 0", name="ck_persona_has_a_unit"),
        sa.CheckConstraint(
            "entity_kind IN ('human', 'nora', 'agent', 'room', 'unit')",
            name="ck_persona_entity_kind",
        ),
        sa.CheckConstraint("revision >= 0", name="ck_persona_revision"),
    )
    op.execute(
        "CREATE TRIGGER nora_row_history AFTER INSERT OR UPDATE OR DELETE "
        "ON \"persona\" FOR EACH ROW EXECUTE FUNCTION nora_row_history('key')"
    )
    op.execute(
        "CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON \"persona\" "
        "FOR EACH STATEMENT EXECUTE FUNCTION nora_row_history_refuse_truncate()"
    )
    op.create_table(
        "persona_line",
        sa.Column("id", pg.UUID(as_uuid=False), primary_key=True),
        sa.Column("persona_key", sa.Text, nullable=False),
        sa.Column("part", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("mode", sa.Text, nullable=False),
        sa.Column("citations", pg.ARRAY(sa.Text), nullable=False),
        sa.Column("draws_on", pg.ARRAY(pg.UUID(as_uuid=False)), nullable=False,
                  server_default=sa.text("'{}'")),
        sa.Column("told_by", sa.Text),
        sa.Column("prediction", sa.Text),
        sa.Column("prediction_observable", sa.Text),
        sa.Column("prediction_window_end", sa.BigInteger),
        sa.Column("state", sa.Text, nullable=False),
        sa.Column("verdict", sa.Text),
        sa.Column("verdict_by", sa.Text),
        sa.Column("verdict_at", sa.BigInteger),
        sa.Column("supersedes", pg.UUID(as_uuid=False)),
        sa.Column("support_count", sa.Integer, nullable=False),
        sa.Column("last_supported_at", sa.BigInteger),
        sa.Column("unit", sa.Text, nullable=False),
        sa.Column("exclude_persons", pg.ARRAY(sa.Text), nullable=False,
                  server_default=sa.text("'{}'")),
        sa.Column("as_of", sa.BigInteger),
        sa.Column("written_at", sa.BigInteger, nullable=False),
        sa.Column("written_by_session", sa.Text, nullable=False),
        _recorded_at(),
        sa.CheckConstraint(
            "part IN ('identity', 'character', 'hypothesis')", name="ck_persona_line_part",
        ),
        sa.CheckConstraint(
            "(part = 'identity' AND kind IN ('name', 'role', 'relationship', 'attribute', "
            "'instruction', 'purpose', 'norm')) OR (part = 'character' AND kind = 'trait') "
            "OR (part = 'hypothesis' AND kind = 'explanation')",
            name="ck_persona_line_kind_part",
        ),
        sa.CheckConstraint(
            "(part = 'identity' AND mode IN ('told', 'deduced')) "
            "OR (part = 'character' AND mode IN ('deduced', 'induced')) "
            "OR (part = 'hypothesis' AND mode = 'abduced')",
            name="ck_persona_line_mode_part",
        ),
        sa.CheckConstraint(
            "length(btrim(text)) > 0 AND length(text) <= 300", name="ck_persona_line_text",
        ),
        sa.CheckConstraint("cardinality(citations) >= 1", name="ck_persona_line_cites"),
        sa.CheckConstraint(
            "(mode = 'told') = (told_by IS NOT NULL)", name="ck_persona_line_told_by",
        ),
        sa.CheckConstraint(
            "CASE WHEN mode = 'abduced' THEN prediction IS NOT NULL "
            "AND prediction_observable IS NOT NULL AND prediction_window_end IS NOT NULL "
            "ELSE prediction IS NULL AND prediction_observable IS NULL "
            "AND prediction_window_end IS NULL END",
            name="ck_persona_line_prediction",
        ),
        sa.CheckConstraint(
            "mode IN ('deduced', 'abduced') OR cardinality(draws_on) = 0",
            name="ck_persona_line_draws_on",
        ),
        sa.CheckConstraint(
            "mode <> 'deduced' OR cardinality(citations) + cardinality(draws_on) >= 2",
            name="ck_persona_line_deduced_premises",
        ),
        sa.CheckConstraint(
            "mode <> 'abduced' OR cardinality(draws_on) >= 1",
            name="ck_persona_line_abduced_explains",
        ),
        sa.CheckConstraint(
            "state IN ('current', 'disputed', 'outranked', 'hidden', 'superseded', "
            "'retired', 'refuted')",
            name="ck_persona_line_state",
        ),
        sa.CheckConstraint(
            "(part = 'hypothesis') = (verdict IS NOT NULL)", name="ck_persona_line_verdict_part",
        ),
        sa.CheckConstraint(
            "verdict IS NULL OR verdict IN ('open', 'confirmed', 'refuted', 'expired', 'unknown')",
            name="ck_persona_line_verdict",
        ),
        sa.CheckConstraint(
            "(verdict_by IS NULL) = (verdict_at IS NULL)", name="ck_persona_line_verdict_by",
        ),
        sa.CheckConstraint(
            "supersedes IS NULL OR supersedes <> id", name="ck_persona_line_not_self",
        ),
        sa.CheckConstraint("support_count >= 1", name="ck_persona_line_support_count"),
        sa.CheckConstraint("length(btrim(unit)) > 0", name="ck_persona_line_has_a_unit"),
        sa.CheckConstraint(
            "length(btrim(written_by_session)) > 0", name="ck_persona_line_names_its_writer",
        ),
    )
    op.create_index("idx_persona_line_key", "persona_line", ["persona_key", "written_at"])
    op.create_index(
        "uq_persona_line_supersedes", "persona_line", ["supersedes"], unique=True,
        postgresql_where=sa.text("supersedes IS NOT NULL"),
    )


def downgrade() -> None:
    # Going back down forgets every Persona line; Character and Culture are untouched.
    op.drop_index("uq_persona_line_supersedes", table_name="persona_line")
    op.drop_index("idx_persona_line_key", table_name="persona_line")
    op.drop_table("persona_line")
    op.execute("DROP TRIGGER IF EXISTS nora_row_history_truncate ON \"persona\"")
    op.execute("DROP TRIGGER IF EXISTS nora_row_history ON \"persona\"")
    op.execute("DELETE FROM row_history WHERE table_name = 'persona'")
    op.drop_table("persona")
