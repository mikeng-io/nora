"""Managed identity and scoped access tables (`design/managed-identity-access/`).

One canonical person across accounts and platforms; a matchable identifier
has exactly one owner, enforced by the identifier table's primary key —
Postgres, not a read-back, is what makes concurrent discovery safe.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from nora.infra.sql.metadata import METADATA

MANAGED_PERSON = sa.Table(
    "managed_person", METADATA,
    sa.Column("person_id", sa.Text, primary_key=True),
    # active | suspended — the installation's global hold, kept DISTINCT
    # from a revoked binding or a room the person left.
    sa.Column("lifecycle", sa.Text, nullable=False, server_default="active"),
    # The person↔Kratos correlation. UNIQUE: one kratos identity maps to at
    # most one person, so two persons can never silently share a record.
    sa.Column("kratos_identity_id", sa.Text, unique=True),
    sa.Column("display_name", sa.Text, nullable=False, server_default=""),
    sa.Column("revision", sa.BigInteger, nullable=False, server_default="1"),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
              server_default=sa.func.now()),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
              server_default=sa.func.now()),
    sa.CheckConstraint(
        "lifecycle IN ('active','suspended')",
        name="ck_managed_person_lifecycle"),
)

MANAGED_IDENTITY_HOLD = sa.Table(
    "managed_identity_hold", METADATA,
    sa.Column("person_id", sa.Text,
              sa.ForeignKey("managed_person.person_id", ondelete="CASCADE",
                            name="managed_identity_hold_person_fkey"), primary_key=True),
    sa.Column("operation_id", sa.Text, primary_key=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
              server_default=sa.func.now()),
)

MANAGED_ACCOUNT = sa.Table(
    "managed_account", METADATA,
    sa.Column("account_id", pg.UUID(as_uuid=False), primary_key=True),
    sa.Column("person_id", sa.Text, nullable=False),
    sa.Column("platform", sa.Text, nullable=False),
    sa.Column("label", sa.Text),
    # bound | revoked — a revoked binding ends resolution of its
    # identifiers while keeping the ownership record: one matchable
    # identifier names one account, ever.
    sa.Column("state", sa.Text, nullable=False, server_default="bound"),
    sa.Column("revision", sa.BigInteger, nullable=False, server_default="1"),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
              server_default=sa.func.now()),
    sa.Column("revoked_at", sa.DateTime(timezone=True)),
    sa.ForeignKeyConstraint(
        ["person_id"], ["managed_person.person_id"],
        name="managed_account_person_fkey", ondelete="RESTRICT"),
    sa.CheckConstraint(
        "state IN ('bound','revoked')",
        name="ck_managed_account_state"),
)

MANAGED_IDENTIFIER = sa.Table(
    "managed_identifier", METADATA,
    sa.Column("platform", sa.Text, nullable=False),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("value", sa.Text, nullable=False),
    sa.Column("account_id", pg.UUID(as_uuid=False), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
              server_default=sa.func.now()),
    # The PRIMARY KEY IS the ownership rule: (platform, kind, value) names
    # exactly one account — a second claim is a constraint violation read
    # back as IdentifierConflictError, never a merge.
    sa.PrimaryKeyConstraint("platform", "kind", "value",
                            name="pk_managed_identifier"),
    sa.ForeignKeyConstraint(
        ["account_id"], ["managed_account.account_id"],
        name="managed_identifier_account_fkey", ondelete="RESTRICT"),
)

IDX_MANAGED_IDENTIFIER_ACCOUNT = sa.Index(
    "idx_managed_identifier_account", MANAGED_IDENTIFIER.c.account_id,
)

MANAGED_ALIAS = sa.Table(
    "managed_alias", METADATA,
    # A retired person id → the surviving canonical person, live persons
    # only: `add_alias` refuses to shadow a live row and consolidation
    # (Task 6) is what retires one — history resolves here, never rewritten.
    sa.Column("former_person_id", sa.Text, primary_key=True),
    sa.Column("person_id", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
              server_default=sa.func.now()),
    sa.ForeignKeyConstraint(
        ["person_id"], ["managed_person.person_id"],
        name="managed_alias_person_fkey", ondelete="RESTRICT"),
)

MANAGED_MEMBERSHIP = sa.Table(
    "managed_membership", METADATA,
    sa.Column("person_id", sa.Text, nullable=False),
    sa.Column("scope_kind", sa.Text, nullable=False),
    sa.Column("scope_id", sa.Text, nullable=False),
    # member | left — leaving is a state, not a delete, so the provenance
    # (which path established it) survives a rejoin.
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
    sa.CheckConstraint(
        "scope_kind IN ('workspace','unit','room')",
        name="ck_managed_membership_scope_kind"),
    sa.CheckConstraint(
        "state IN ('member','left')",
        name="ck_managed_membership_state"),
)

IDX_MANAGED_MEMBERSHIP_SCOPE = sa.Index(
    "idx_managed_membership_scope",
    MANAGED_MEMBERSHIP.c.scope_kind, MANAGED_MEMBERSHIP.c.scope_id,
)

# The live supports behind a membership row (`identity.md`): membership
# standing derives from these — a roster departure retracts only its own
# support, so an independently established membership survives it.
MANAGED_MEMBERSHIP_SUPPORT = sa.Table(
    "managed_membership_support", METADATA,
    sa.Column("person_id", sa.Text, nullable=False),
    sa.Column("scope_kind", sa.Text, nullable=False),
    sa.Column("scope_id", sa.Text, nullable=False),
    sa.Column("source", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
              server_default=sa.func.now()),
    sa.PrimaryKeyConstraint("person_id", "scope_kind", "scope_id", "source",
                            name="pk_managed_membership_support"),
    sa.ForeignKeyConstraint(
        ["person_id"], ["managed_person.person_id"],
        name="managed_membership_support_person_fkey", ondelete="RESTRICT"),
)

MANAGED_OPERATION = sa.Table(
    "managed_operation", METADATA,
    sa.Column("operation_id", sa.Text, primary_key=True),
    sa.Column("kind", sa.Text, nullable=False),
    # pending | incomplete | complete | blocked — see OperationJournalState:
    # `incomplete` means part of the effect committed and reconciliation is
    # still owed; it is never reported as `complete`.
    sa.Column("state", sa.Text, nullable=False),
    # Named store effects and their state (`pending`/`done`/`skipped`), so
    # reconciliation resumes the exact step a crash or outage left.
    sa.Column("steps", pg.JSONB, nullable=False,
              server_default=sa.text("'{}'::jsonb")),
    # The intended effect the operation_id is bound to — replay with a
    # different snapshot is a conflict, not a retry.
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

IDX_MANAGED_OPERATION_STATE = sa.Index(
    "idx_managed_operation_state", MANAGED_OPERATION.c.state,
)

MANAGED_AUTHORITY_BLOCK = sa.Table(
    "managed_authority_block", METADATA,
    # A failed revocation blocks the affected (person, scope) authority
    # locally until reconciliation confirms it — persisted across restarts
    # and consulted FIRST on check, so a stale grant can never serve.
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

# Pending bilateral consent is separate from published account ownership.
MANAGED_LINK_REQUEST = sa.Table(
    "managed_link_request", METADATA,
    sa.Column("request_id", sa.Text, primary_key=True),
    sa.Column("snapshot", pg.JSONB, nullable=False),
)

MANAGED_LINK_DECISION = sa.Table(
    "managed_link_decision", METADATA,
    sa.Column("operation_id", sa.Text, primary_key=True),
    sa.Column("request_id", sa.Text,
              sa.ForeignKey("managed_link_request.request_id", ondelete="RESTRICT",
                            name="managed_link_decision_request_fkey"),
              nullable=False),
    sa.Column("effect_id", sa.Text, nullable=False),
    sa.Column("evidence", pg.JSONB, nullable=False),
)

IDX_MANAGED_LINK_DECISION_REQUEST = sa.Index(
    "idx_managed_link_decision_request", MANAGED_LINK_DECISION.c.request_id,
)
