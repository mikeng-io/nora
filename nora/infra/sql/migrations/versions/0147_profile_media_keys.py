"""Extract attachment addresses once, then join the owning media primary key.

No application rows or hot-table indexes change. Windows retain the historical
substring semantics for canonical SHA-256 addresses, including legacy non-JSON
references and overlapping addresses. The array result exposes a small relation
without multiplying function-row estimates by room history and enabling JIT.
"""
from alembic import op

revision = '0147_profile_media_keys'
down_revision = '0146_multi_post_native_ids'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION nora_media_keys(ref text) RETURNS text[]
        LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT AS $$
            SELECT ARRAY(
                SELECT DISTINCT substr(ref, pos, 64)
                FROM generate_series(1, length(ref) - 63) AS pos
                WHERE substr(ref, pos, 64) ~ '^[0-9a-f]{64}$'
            )
        $$
    """)


def downgrade() -> None:
    op.execute('DROP FUNCTION nora_media_keys(text)')
