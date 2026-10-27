"""Keep provenance drop counts per extraction attempt, without claim content."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0136_extraction_drops"
down_revision = "0135_extraction_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("extraction_attempt", sa.Column(
        "dropped_claims", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb"),
    ))


def downgrade() -> None:
    op.drop_column("extraction_attempt", "dropped_claims")
