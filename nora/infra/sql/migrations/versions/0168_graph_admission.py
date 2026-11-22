"""Declare the inferred entity merge edge."""

from alembic import op

revision = "0168_graph_admission"
down_revision = "0167_presence_place_unit_key"
branch_labels = None
depends_on = None

_TYPES = (
    "'cites', 'about', 'draws_on', 'rests_on', 'serves', 'watches', 'closed_by', 'interested_in'"
)


def upgrade() -> None:
    op.drop_constraint("ck_edge_type", "edge", type_="check")
    op.create_check_constraint("ck_edge_type", "edge", f"type IN ({_TYPES}, 'merged_into')")


def downgrade() -> None:
    op.drop_constraint("ck_edge_type", "edge", type_="check")
    op.create_check_constraint("ck_edge_type", "edge", f"type IN ({_TYPES})")
