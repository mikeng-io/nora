"""Retain the original exact account and binding of a delivered OTP."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0159_otp_account_proof"
down_revision = "0158_membership_supports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("otp_challenge", sa.Column(
        "account_proof", postgresql.JSONB(none_as_null=True), nullable=True))


def downgrade() -> None:
    op.drop_column("otp_challenge", "account_proof")
