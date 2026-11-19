"""Track each graph stimulus's claims per consumer."""

import sqlalchemy as sa
from alembic import op

revision = "0178_graph_stimulus_claim"
down_revision = "0177_debrief_obligation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "graph_stimulus_claim",
        sa.Column("unit", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("graph_id", sa.Text, nullable=False),
        sa.Column("occurrence", sa.Text, nullable=False),
        sa.Column("claimant", sa.Text, nullable=False),
        sa.Column("pass_id", sa.Text, nullable=False),
        sa.Column("state", sa.Text, nullable=False),
        sa.Column("claimed", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settled", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("unit", "kind", "graph_id", "occurrence", "claimant",
                                name="pk_graph_stimulus_claim"),
        sa.ForeignKeyConstraint(
            ("unit", "kind", "graph_id", "occurrence"),
            tuple(f"graph_stimulus.{key}" for key in ("unit", "kind", "graph_id", "occurrence")),
            name="fk_graph_stimulus_claim_stimulus",
        ),
        sa.UniqueConstraint("unit", "pass_id", name="uq_graph_stimulus_claim_pass"),
        sa.CheckConstraint("state IN ('claimed', 'closed', 'released')",
                           name="ck_graph_stimulus_claim_state"),
        sa.CheckConstraint("(state = 'claimed') = (settled IS NULL)",
                           name="ck_graph_stimulus_claim_settled"),
    )
    op.execute("""
        CREATE TRIGGER nora_row_history AFTER INSERT OR UPDATE OR DELETE ON graph_stimulus_claim
        FOR EACH ROW EXECUTE FUNCTION nora_row_history(
            'unit', 'kind', 'graph_id', 'occurrence', 'claimant'
        )
    """)
    op.execute("""
        CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON graph_stimulus_claim
        FOR EACH STATEMENT EXECUTE FUNCTION nora_row_history_refuse_truncate()
    """)
    op.add_column("nora_pass_output", sa.Column("silence_reason", sa.Text))
    op.add_column("nora_pass_output", sa.Column("authority_intention", sa.Text))


def downgrade() -> None:
    op.drop_column("nora_pass_output", "authority_intention")
    op.drop_column("nora_pass_output", "silence_reason")
    op.drop_table("graph_stimulus_claim")
