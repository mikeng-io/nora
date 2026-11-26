"""Managed identity and scoped access — the binding store's tables.

docs/design/managed-identity-access/. `managed_person` is the canonical person
with its Kratos correlation; `managed_account` + `managed_identifier` carry
provider-qualified bindings where one matchable `(platform, kind, value)`
identifier names exactly one account — the primary key IS the ownership
rule. `managed_alias` resolves retired person ids to survivors, keeping
historical references live. `managed_membership` is (person, scope)
standing with establishment provenance. `managed_operation` journals the
intended effect and per-step state for operations spanning Nora/Kratos/Keto
so a partial result stays `incomplete` and resumable across restarts.
`managed_authority_block` persists the local block a failed revocation
raises on the affected (person, scope) — the check path consults it first.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0155_managed_identity"
down_revision = "0154_event_revision_citations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _person_and_bindings()
    _alias()
    _membership()
    _journal_and_blocks()


def _person_and_bindings() -> None:
    op.create_table(
        "managed_person",
        sa.Column("person_id", sa.Text, primary_key=True),
        sa.Column("lifecycle", sa.Text, nullable=False, server_default="active"),
        sa.Column("kratos_identity_id", sa.Text, unique=True),
        sa.Column("display_name", sa.Text, nullable=False, server_default=""),
        sa.Column("revision", sa.BigInteger, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint("lifecycle IN ('active','suspended')",
                           name="ck_managed_person_lifecycle"),
    )
    op.create_table(
        "managed_account",
        sa.Column("account_id", pg.UUID(as_uuid=False), primary_key=True),
        sa.Column("person_id", sa.Text, nullable=False),
        sa.Column("platform", sa.Text, nullable=False),
        sa.Column("label", sa.Text),
        sa.Column("state", sa.Text, nullable=False, server_default="bound"),
        sa.Column("revision", sa.BigInteger, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["person_id"], ["managed_person.person_id"],
            name="managed_account_person_fkey", ondelete="RESTRICT"),
        sa.CheckConstraint("state IN ('bound','revoked')",
                           name="ck_managed_account_state"),
    )
    op.create_table(
        "managed_identifier",
        sa.Column("platform", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("account_id", pg.UUID(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("platform", "kind", "value",
                                name="pk_managed_identifier"),
        sa.ForeignKeyConstraint(
            ["account_id"], ["managed_account.account_id"],
            name="managed_identifier_account_fkey", ondelete="RESTRICT"),
    )
    op.create_index(
        "idx_managed_identifier_account",
        "managed_identifier", ["account_id"],
    )


def _alias() -> None:
    op.create_table(
        "managed_alias",
        sa.Column("former_person_id", sa.Text, primary_key=True),
        sa.Column("person_id", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["person_id"], ["managed_person.person_id"],
            name="managed_alias_person_fkey", ondelete="RESTRICT"),
    )


def _membership() -> None:
    op.create_table(
        "managed_membership",
        sa.Column("person_id", sa.Text, nullable=False),
        sa.Column("scope_kind", sa.Text, nullable=False),
        sa.Column("scope_id", sa.Text, nullable=False),
        sa.Column("state", sa.Text, nullable=False),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("revision", sa.BigInteger, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("person_id", "scope_kind", "scope_id",
                                name="pk_managed_membership"),
        sa.ForeignKeyConstraint(
            ["person_id"], ["managed_person.person_id"],
            name="managed_membership_person_fkey", ondelete="RESTRICT"),
        sa.CheckConstraint("scope_kind IN ('workspace','unit','room')",
                           name="ck_managed_membership_scope_kind"),
        sa.CheckConstraint("state IN ('member','left')",
                           name="ck_managed_membership_state"),
    )
    op.create_index(
        "idx_managed_membership_scope",
        "managed_membership", ["scope_kind", "scope_id"],
    )


def _journal_and_blocks() -> None:
    op.create_table(
        "managed_operation",
        sa.Column("operation_id", sa.Text, primary_key=True),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("state", sa.Text, nullable=False),
        sa.Column("steps", pg.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("snapshot", pg.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("outcome", sa.Text, nullable=False, server_default=""),
        sa.Column("detail", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint(
            "state IN ('pending','incomplete','complete','blocked')",
            name="ck_managed_operation_state"),
    )
    op.create_index(
        "idx_managed_operation_state",
        "managed_operation", ["state"],
    )
    op.create_table(
        "managed_authority_block",
        sa.Column("person_id", sa.Text, nullable=False),
        sa.Column("scope_kind", sa.Text, nullable=False),
        sa.Column("scope_id", sa.Text, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("operation_id", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("person_id", "scope_kind", "scope_id",
                                name="pk_managed_authority_block"),
    )


def downgrade() -> None:
    op.drop_table("managed_authority_block")
    op.drop_index("idx_managed_operation_state", table_name="managed_operation")
    op.drop_table("managed_operation")
    op.drop_index("idx_managed_membership_scope", table_name="managed_membership")
    op.drop_table("managed_membership")
    op.drop_table("managed_alias")
    op.drop_index("idx_managed_identifier_account", table_name="managed_identifier")
    op.drop_table("managed_identifier")
    op.drop_table("managed_account")
    op.drop_table("managed_person")
