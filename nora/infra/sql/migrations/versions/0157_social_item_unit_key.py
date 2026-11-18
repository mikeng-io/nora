"""Deduplicate a social observation independently within each observing unit."""

from alembic import op

revision = "0157_social_item_unit_key"
down_revision = "0156_graph_registration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("uq_social_item_key", table_name="social_item")
    op.create_index(
        "uq_social_item_key", "social_item", ["network", "item_id", "unit"],
        unique=True, postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    op.drop_index("uq_social_item_key", table_name="social_item")
    op.create_index("uq_social_item_key", "social_item", ["network", "item_id"], unique=True)
