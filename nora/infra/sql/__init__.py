"""PostgreSQL persistence: schema, engine, record store, migrations.

Public surface is `__all__` below — `METADATA`, `MESSAGES`, `SqlRecordStore`,
`create_sql_engine`, `create_readonly_sql_engine`, `current_revision`,
`head_revision`, `revision_is_known`, `run_migrations`, `stamp_head` (see that
name's own note for why it is exported with no in-repo caller), and
`SourceRevisionMismatchError`. Production code (`nora/__main__.py`,
`nora/app/composition.py`) imports from this package root. The test suite and
`tests/integration/harness.py` import `nora.infra.sql.audit`,
`nora.infra.sql.engine`, and `nora.infra.sql.migrations_runner` directly, and
`audit.py` imports its sibling `migrations_runner` directly too.
"""

from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import (
    create_readonly_sql_engine,
    create_sql_engine,
)
from nora.infra.sql.migrations_runner import (
    SourceRevisionMismatchError,
    current_revision,
    head_revision,
    revision_is_known,
    run_migrations,
    stamp_head,
)
from nora.infra.sql.schema import MESSAGES, METADATA

# `stamp_head` has no in-repo caller: it exists for the operator to run once, by
# hand, against an already-correct database at cutover. Kept on
# the public surface deliberately rather than dropped, so an operator doing that
# finds it here instead of reaching into the submodule.
__all__ = [
    "MESSAGES",
    "METADATA",
    "SourceRevisionMismatchError",
    "SqlRecordStore",
    "create_readonly_sql_engine",
    "create_sql_engine",
    "current_revision",
    "head_revision",
    "revision_is_known",
    "run_migrations",
    "stamp_head",
]
