"""SQLite persistence: schema, engine, audit store, migrations.

Public surface is `__all__` below — `METADATA`, `MESSAGES`, `SqlAuditStore`,
`async_url_for`, `create_sql_engine`, `current_revision`, `head_revision`,
`run_migrations`, and `stamp_head` (see that name's own note for why it is
exported with no in-repo caller). Production code (`nora/__main__.py`,
`nora/app/composition.py`) imports from this package root, never from a
submodule — that is the rule this package intends. It is not mechanically
enforced, though, and is not honoured in practice: the test suite and
`tests/integration/harness.py` import `nora.infra.sql.audit`,
`nora.infra.sql.engine`, and `nora.infra.sql.migrations_runner` directly
rather than through this root, and `audit.py:22` does the same internally,
importing its sibling `migrations_runner` submodule rather than going back
through this `__init__`.
"""

from nora.infra.sql.audit import SqlAuditStore
from nora.infra.sql.engine import async_url_for, create_sql_engine
from nora.infra.sql.migrations_runner import (
    current_revision,
    head_revision,
    run_migrations,
    stamp_head,
)
from nora.infra.sql.schema import MESSAGES, METADATA

# `stamp_head` has no in-repo caller: it exists for the operator to run once,
# by hand, against an already-correct database (used on the live 0-row
# `data/messages.db`, per Task 10 — see its own docstring in
# `migrations_runner.py`). Kept on the public surface deliberately rather
# than dropped, so an operator doing that again later finds it here instead
# of reaching into the submodule.
__all__ = [
    "MESSAGES",
    "METADATA",
    "SqlAuditStore",
    "async_url_for",
    "create_sql_engine",
    "current_revision",
    "head_revision",
    "run_migrations",
    "stamp_head",
]
