"""Allow one reflection pass to claim a set of stimuli."""

import sqlalchemy as sa
from alembic import op

revision = "0181_wake_claims"
down_revision = "0179_proactive_recheck"
branch_labels = None
depends_on = None

_TABLE = "graph_stimulus_claim"
_UNIQUE = "uq_graph_stimulus_claim_pass"


def upgrade() -> None:
    op.drop_constraint(_UNIQUE, _TABLE, type_="unique")


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM graph_stimulus_claim WHERE claimant = 'wake'"))
    op.create_unique_constraint(_UNIQUE, _TABLE, ["unit", "pass_id"])
