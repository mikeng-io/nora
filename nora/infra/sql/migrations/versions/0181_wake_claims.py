"""Allow one reflection pass to claim a set of stimuli."""

import sqlalchemy as sa
from alembic import op

revision = "0181_wake_claims"
down_revision = "0179_proactive_recheck"
branch_labels = None
depends_on = None

_TABLE = "graph_stimulus_claim"
_UNIQUE = "uq_graph_stimulus_claim_pass"
_DEBRIEF = "debrief_obligation"
_STATE = "ck_debrief_obligation_state"


def upgrade() -> None:
    op.drop_constraint(_UNIQUE, _TABLE, type_="unique")
    op.drop_constraint(_STATE, _DEBRIEF, type_="check")
    op.create_check_constraint(_STATE, _DEBRIEF, "state IN ('pending', 'settled', 'parked')")


def downgrade() -> None:
    op.drop_constraint(_STATE, _DEBRIEF, type_="check")
    op.execute(sa.text("UPDATE debrief_obligation SET state = 'pending' WHERE state = 'parked'"))
    op.create_check_constraint(_STATE, _DEBRIEF, "state IN ('pending', 'settled')")
    op.execute(sa.text("DELETE FROM graph_stimulus_claim WHERE claimant = 'wake'"))
    op.create_unique_constraint(_UNIQUE, _TABLE, ["unit", "pass_id"])
