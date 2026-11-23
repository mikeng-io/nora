"""Store independent grades of guesses and lessons."""

import sqlalchemy as sa
from alembic import op

revision = "0183_claim_grade"
down_revision = "0182_graph_replay"
branch_labels = None
depends_on = None

_PARTS = "'fact', 'trait', 'guess', 'lesson', 'situation'"
_EDGES = "'cites', 'about', 'draws_on', 'rests_on', 'serves', 'watches', 'closed_by', 'merged_into'"


def _checks(parts: str, edges: str) -> None:
    op.drop_constraint("ck_claim_part", "claim", type_="check")
    op.create_check_constraint("ck_claim_part", "claim", f"part IN ({parts})")
    op.drop_constraint("ck_edge_type", "edge", type_="check")
    op.create_check_constraint("ck_edge_type", "edge", f"type IN ({edges})")


def upgrade() -> None:
    op.add_column("claim", sa.Column("grade", sa.Text()))
    _checks(f"{_PARTS}, 'grade'", f"{_EDGES}, 'grades'")
    op.create_check_constraint(
        "ck_claim_grade", "claim",
        "CASE WHEN part = 'grade' AND state <> 'tombstoned' THEN grade IS NOT NULL AND grade IN "
        "('confirmed', 'refuted', 'expired', 'holds', 'weakened') ELSE grade IS NULL END",
    )


def downgrade() -> None:
    op.drop_constraint("ck_claim_grade", "claim", type_="check")
    _checks(_PARTS, _EDGES)
    op.drop_column("claim", "grade")
