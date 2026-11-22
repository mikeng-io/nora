"""Record the account privacy each social probe answered."""

import sqlalchemy as sa
from alembic import op

revision = "0184_social_poll_privacy"
down_revision = "0183_claim_grade"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("social_poll", sa.Column("is_private", sa.Boolean()))


def downgrade() -> None:
    op.drop_column("social_poll", "is_private")
