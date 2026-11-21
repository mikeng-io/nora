"""Add lesson and situation claim parts and remove the interest edge type."""

from alembic import op

revision = "0176_claim_parts"
down_revision = "0175_graph_search"
branch_labels = None
depends_on = None

_PARTS = "'fact', 'trait', 'guess'"
_EDGES = "'cites', 'about', 'draws_on', 'rests_on', 'serves', 'watches', 'closed_by'"


def _checks(parts: str, edges: str) -> None:
    op.drop_constraint("ck_claim_part", "claim", type_="check")
    op.create_check_constraint("ck_claim_part", "claim", f"part IN ({parts})")
    op.drop_constraint("ck_edge_type", "edge", type_="check")
    op.create_check_constraint("ck_edge_type", "edge", f"type IN ({edges})")


def upgrade() -> None:
    _checks(f"{_PARTS}, 'lesson', 'situation'", f"{_EDGES}, 'merged_into'")


def downgrade() -> None:
    _checks(_PARTS, f"{_EDGES}, 'interested_in', 'merged_into'")
