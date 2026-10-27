"""Retain terminal extraction failures for operator reconciliation."""

from alembic import op

revision = "0134_extraction_failed"
down_revision = "0133_standing_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_extraction_state", "extraction", type_="check")
    op.create_check_constraint(
        "ck_extraction_state", "extraction", "state IN ('pending', 'done', 'retired', 'failed')",
    )


def downgrade() -> None:
    # The old worker can retry these rows; preserve snapshots and attempt history.
    op.execute("UPDATE extraction SET state = 'pending' WHERE state = 'failed'")
    op.drop_constraint("ck_extraction_state", "extraction", type_="check")
    op.create_check_constraint(
        "ck_extraction_state", "extraction", "state IN ('pending', 'done', 'retired')",
    )
