"""One long-lived async engine over `postgresql+asyncpg://…`.

Nothing here is file-shaped: a URL has no parent directory to create, no
PRAGMAs are set on connect, and the read-only engine's guarantee is enforced
by Postgres, not by a URI flag.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def create_sql_engine(url: str) -> AsyncEngine:
    """The application's read/write engine. `url` is a full
    `postgresql+asyncpg://…` URL (from `DATABASE_URL`, DEC-8)."""
    return create_async_engine(url)


def create_readonly_sql_engine(url: str) -> AsyncEngine:
    """An engine that CANNOT write, for the operator control plane.

    `nora groups` and `nora persons pending` are strictly read-only, and the
    honest way to say so is to make a write impossible rather than to promise one
    is never issued. Postgres enforces it: every connection opens with
    `default_transaction_read_only=on`, so any INSERT/UPDATE/DELETE raises
    `ReadOnlySqlTransaction` at the server, and NOT a caller keeping a promise.

    In the deployment this engine is additionally pointed at a `GRANT SELECT`-only
    role whose write privileges do not exist at all; that role is
    defence in depth on top of the session setting, and this engine works over
    either a read-only role's URL or the write role's — the read-only guarantee
    here does not depend on which.
    """
    return create_async_engine(
        url,
        connect_args={"server_settings": {"default_transaction_read_only": "on"}},
    )
