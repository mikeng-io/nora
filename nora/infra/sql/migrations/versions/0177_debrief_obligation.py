"""Retain debrief obligations and individual tool calls made inside a pass."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0177_debrief_obligation"
down_revision = "0176_claim_parts"
branch_labels = None
depends_on = None


def _output_checks(kinds: str, content: str) -> None:
    table = "nora_pass_output"
    for suffix, expression in (("kind", f"output_kind IN ({kinds})"), ("content", content)):
        name = f"ck_nora_pass_output_{suffix}"
        op.drop_constraint(name, table, type_="check")
        op.create_check_constraint(name, table, expression)


def upgrade() -> None:
    _output_checks("'expression', 'silence', 'tool_call'",
                   "(output_kind <> 'silence') = (content IS NOT NULL)")
    op.create_table(
        "debrief_obligation",
        sa.Column("unit", sa.Text, primary_key=True),
        sa.Column("pass_id", sa.Text, primary_key=True),
        sa.Column("room", sa.Text, nullable=False),
        sa.Column("occasion", sa.Text, nullable=False),
        sa.Column("intention", sa.Text),
        sa.Column("state", sa.Text, nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settled", sa.DateTime(timezone=True)),
        sa.Column("first_message_id", sa.BigInteger),
        sa.Column("last_message_id", sa.BigInteger),
        sa.Column("proposal_ids", pg.JSONB(none_as_null=True)),
        sa.CheckConstraint("state IN ('pending', 'settled')", name="ck_debrief_obligation_state"),
        sa.CheckConstraint("(state = 'settled') = (settled IS NOT NULL)",
                           name="ck_debrief_obligation_settled"),
        sa.CheckConstraint("attempts >= 0", name="ck_debrief_obligation_attempts"),
        sa.CheckConstraint("(first_message_id IS NULL) = (last_message_id IS NULL)",
                           name="ck_debrief_obligation_window"),
    )
    op.create_index(
        "idx_debrief_obligation_room", "debrief_obligation", ("unit", "room", "created"),
    )
    op.execute("""
        CREATE TRIGGER nora_row_history AFTER INSERT OR UPDATE OR DELETE
        ON debrief_obligation FOR EACH ROW EXECUTE FUNCTION nora_row_history('unit', 'pass_id')
    """)
    op.execute("""
        CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON debrief_obligation
        FOR EACH STATEMENT EXECUTE FUNCTION nora_row_history_refuse_truncate()
    """)


def downgrade() -> None:
    op.drop_table("debrief_obligation")
    # The append-only trigger guards application writes; this rollback removes rows
    # only the widened constraint being reverted could admit.
    op.execute("ALTER TABLE nora_pass_output DISABLE TRIGGER nora_pass_output_append_only")
    op.execute("""
        WITH dropped AS (
            DELETE FROM nora_pass_output WHERE output_kind = 'tool_call'
            RETURNING graph_id, unit, md5(content::text) AS content_hash
        )
        UPDATE registry SET state = 'tombstoned', tombstoned_at = clock_timestamp(),
            content_hash = dropped.content_hash
        FROM dropped WHERE registry.id = dropped.graph_id AND registry.unit = dropped.unit
    """)
    op.execute("ALTER TABLE nora_pass_output ENABLE TRIGGER nora_pass_output_append_only")
    _output_checks("'expression', 'silence'",
                   "(output_kind = 'expression') = (content IS NOT NULL)")
