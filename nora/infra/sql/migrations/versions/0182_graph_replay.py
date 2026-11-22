"""Record one immutable receipt for each closed historical graph wake."""

from alembic import op

revision = "0182_graph_replay"
down_revision = "0182_lesson_tally"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE graph_replay_receipt (
            unit TEXT NOT NULL,
            pass_id TEXT NOT NULL,
            clock TIMESTAMP WITH TIME ZONE NOT NULL,
            inputs JSONB NOT NULL,
            recorded_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
            CONSTRAINT pk_graph_replay_receipt PRIMARY KEY (unit, pass_id)
        )
    """)
    op.execute("""
        CREATE TRIGGER nora_graph_replay_receipt_append_only
        BEFORE UPDATE OR DELETE ON graph_replay_receipt
        FOR EACH ROW EXECUTE FUNCTION nora_refuse_act_mutation()
    """)
    op.execute("""
        CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON graph_replay_receipt
        FOR EACH STATEMENT EXECUTE FUNCTION nora_row_history_refuse_truncate()
    """)


def downgrade() -> None:
    op.drop_table("graph_replay_receipt")
