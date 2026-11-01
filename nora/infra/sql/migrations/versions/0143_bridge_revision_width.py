"""Store the projection contract's 53-bit admission revision without narrowing."""

import sqlalchemy as sa
from alembic import op

revision = "0143_bridge_revision_width"
down_revision = "0142_delivery_uncertain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "bridge_event", "admission_rev",
        existing_type=sa.Integer(), type_=sa.BigInteger(), existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "bridge_event", "admission_rev",
        existing_type=sa.BigInteger(), type_=sa.Integer(), existing_nullable=True,
    )
