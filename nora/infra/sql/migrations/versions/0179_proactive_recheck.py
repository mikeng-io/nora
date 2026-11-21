"""Count unavailable rechecks and allow the proactive claim to park."""

import sqlalchemy as sa
from alembic import op

revision = "0179_proactive_recheck"
down_revision = "0178_graph_stimulus_claim"
branch_labels = None
depends_on = None

_TABLE = "graph_stimulus_claim"
_STATE = "ck_graph_stimulus_claim_state"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("attempts", sa.Integer, nullable=False, server_default="0"))
    op.drop_constraint(_STATE, _TABLE, type_="check")
    op.create_check_constraint(
        _STATE, _TABLE, "state IN ('claimed', 'closed', 'released', 'parked')",
    )
    op.add_column("nora_pass_output", sa.Column("recheck_reason", sa.Text))


def downgrade() -> None:
    op.drop_column("nora_pass_output", "recheck_reason")
    op.drop_constraint(_STATE, _TABLE, type_="check")
    op.execute(sa.text("UPDATE graph_stimulus_claim SET state = 'released' WHERE state = 'parked'"))
    op.create_check_constraint(_STATE, _TABLE, "state IN ('claimed', 'closed', 'released')")
    op.drop_column(_TABLE, "attempts")
