"""Keep item tombstone times and expired coverage."""

import sqlalchemy as sa
from alembic import op

revision = "0172_expired_coverage"
down_revision = "0171_watch_results"
branch_labels = None
depends_on = None

_NEW_HISTORY = {"tool_call_log": "created_at", "watch_result": "asserted_at"}


def upgrade() -> None:
    op.add_column("registry", sa.Column("occurred_at", sa.DateTime(timezone=True)))
    op.create_table(
        "expired_coverage",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("unit", sa.Text, nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("person", sa.Text),
        sa.Column("from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("to", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('"from" <= "to"', name="ck_expired_coverage_order"),
    )
    op.create_index("idx_expired_coverage_unit_to", "expired_coverage", ["unit", "to"])
    op.execute("LOCK TABLE tool_call_log, watch_result IN SHARE ROW EXCLUSIVE MODE")
    for table, clock in _NEW_HISTORY.items():
        op.execute(
            "INSERT INTO row_history (table_name, row_key, op, image, recorded_at) "  # noqa: S608
            f"SELECT '{table}', jsonb_build_object('id', x.id), 'backfill', "
            f"to_jsonb(x), x.{clock} FROM {table} AS x"
        )
    for table in ("expired_coverage", *_NEW_HISTORY):
        op.execute(
            "CREATE TRIGGER nora_row_history AFTER INSERT OR UPDATE OR DELETE "
            f"ON {table} FOR EACH ROW EXECUTE FUNCTION nora_row_history('id')"
        )
        op.execute(
            "CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE "
            f"ON {table} FOR EACH STATEMENT EXECUTE FUNCTION nora_row_history_refuse_truncate()"
        )


def downgrade() -> None:
    for table in _NEW_HISTORY:
        op.execute(f"DROP TRIGGER nora_row_history_truncate ON {table}")
        op.execute(f"DROP TRIGGER nora_row_history ON {table}")
    op.execute("""
        DELETE FROM row_history
        WHERE table_name IN ('expired_coverage', 'tool_call_log', 'watch_result')
    """)
    op.drop_table("expired_coverage")
    op.drop_column("registry", "occurred_at")
