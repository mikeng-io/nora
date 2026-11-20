"""The canonical person's scoped descriptive names (`authorization.md`)."""

import sqlalchemy as sa
from alembic import op

revision = "0162_scoped_names"
down_revision = "0161_observation_ordering"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "managed_scoped_name",
        sa.Column("person_id", sa.Text, nullable=False),
        sa.Column("scope_kind", sa.Text, nullable=False),
        sa.Column("scope_id", sa.Text, nullable=False),
        sa.Column("nickname", sa.Text, nullable=False),
        sa.Column("operation_id", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["operation_id"], ["managed_operation.operation_id"],
            name="managed_scoped_name_operation_fkey", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["person_id"], ["managed_person.person_id"],
            name="managed_scoped_name_person_fkey", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint(
            "person_id", "scope_kind", "scope_id",
            name="pk_managed_scoped_name"),
        sa.CheckConstraint(
            "scope_kind IN ('workspace','unit','room')",
            name="ck_managed_scoped_name_scope_kind"),
    )


def downgrade() -> None:
    op.drop_table("managed_scoped_name")
