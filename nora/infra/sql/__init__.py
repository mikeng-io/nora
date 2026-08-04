"""SQLite persistence: schema, engine, audit store, migrations.

Public surface: `METADATA`, `MESSAGES`, `create_sql_engine`, `SqlAuditStore`,
`run_migrations`. Import from this package root, never from a submodule.
"""

from nora.infra.sql.migrations_runner import (
    current_revision,
    head_revision,
    run_migrations,
    stamp_head,
)
from nora.infra.sql.schema import MESSAGES, METADATA

__all__ = [
    "MESSAGES",
    "METADATA",
    "current_revision",
    "head_revision",
    "run_migrations",
    "stamp_head",
]
