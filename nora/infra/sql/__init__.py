"""SQLite persistence: schema, engine, audit store, migrations.

Public surface: `METADATA`, `MESSAGES`, `create_sql_engine`, `SqlAuditStore`,
`run_migrations`. Import from this package root, never from a submodule.
"""

from nora.infra.sql.schema import MESSAGES, METADATA

__all__ = ["MESSAGES", "METADATA"]
