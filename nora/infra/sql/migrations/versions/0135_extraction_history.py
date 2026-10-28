"""Distinguish historical extraction sources and retain safe attempt failure stages."""

import sqlalchemy as sa
from alembic import op

revision = "0135_extraction_history"
down_revision = "0134_extraction_failed"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("extraction_queue", sa.Column(
        "historical", sa.Boolean(), nullable=False, server_default=sa.false(),
    ))
    op.add_column("extraction_attempt", sa.Column("failure_stage", sa.Text()))


def downgrade() -> None:
    op.drop_column("extraction_attempt", "failure_stage")
    op.drop_column("extraction_queue", "historical")
