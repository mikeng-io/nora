"""Version mutable managed state; keep private consent deliveries append-only.

Existing mutable heads become known at upgrade, without inventing earlier states.
Private nonces stay in their append-only delivery rows rather than row_history.
"""

import sqlalchemy as sa
from alembic import op

revision = "0163_managed_history"
down_revision = "0162_scoped_names"
branch_labels = None
depends_on = None

_TABLES = {
    "managed_person": ("person_id",),
    "managed_account": ("account_id",),
    "managed_identifier": ("platform", "kind", "value"),
    "managed_alias": ("former_person_id",),
    "managed_membership": ("person_id", "scope_kind", "scope_id"),
    "managed_membership_support": ("person_id", "scope_kind", "scope_id", "source"),
    "managed_operation": ("operation_id",),
    "managed_authority_block": ("person_id", "scope_kind", "scope_id"),
    "managed_identity_hold": ("person_id", "operation_id"),
    "managed_link_request": ("request_id",),
    "managed_observation": ("service", "collection", "scope_kind", "scope_id"),
    "managed_scoped_name": ("person_id", "scope_kind", "scope_id"),
}


def upgrade() -> None:
    tables = ", ".join(f'"{table}"' for table in _TABLES)
    op.execute(f"LOCK TABLE {tables} IN SHARE ROW EXCLUSIVE MODE")
    op.add_column("managed_link_decision", sa.Column(
        "recorded_at", sa.DateTime(timezone=True), nullable=False,
        server_default=sa.text("clock_timestamp()")))
    for table, columns in _TABLES.items():
        pairs = ", ".join(f"'{c}', to_jsonb(x.\"{c}\")" for c in columns)
        op.execute(
            "INSERT INTO row_history (table_name, row_key, op, image, recorded_at) "  # noqa: S608 - frozen table/key names
            f"SELECT '{table}', jsonb_build_object({pairs}), 'backfill', "
            f'to_jsonb(x), clock_timestamp() FROM "{table}" x')
        args = ", ".join(f"'{c}'" for c in columns)
        op.execute(
            "CREATE TRIGGER nora_row_history AFTER INSERT OR UPDATE OR DELETE "
            f'ON "{table}" FOR EACH ROW EXECUTE FUNCTION nora_row_history({args})')
        op.execute(
            f'CREATE TRIGGER nora_row_history_truncate BEFORE TRUNCATE ON "{table}" '
            "FOR EACH STATEMENT EXECUTE FUNCTION nora_row_history_refuse_truncate()")


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f'DROP TRIGGER IF EXISTS nora_row_history ON "{table}"')
        op.execute(f'DROP TRIGGER IF EXISTS nora_row_history_truncate ON "{table}"')
    op.execute("DELETE FROM row_history WHERE table_name IN (" +  # noqa: S608 - frozen table names
               ", ".join(f"'{table}'" for table in _TABLES) + ")")
    op.drop_column("managed_link_decision", "recorded_at")
