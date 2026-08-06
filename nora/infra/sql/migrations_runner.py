"""Programmatic Alembic. Runtime never reads `alembic.ini`.

`alembic.ini` exists at the repo root for CLI use (`uv run alembic check`,
`uv run alembic revision --autogenerate`). The application builds its Config
here instead, so a missing or unmounted ini can never stop Nora from starting.

Migrations run SYNCHRONOUSLY (Alembic is a sync tool), against a
`postgresql+psycopg://` URL, while the application itself talks to the same
database over `postgresql+asyncpg://`. `sync_url_for` derives the sync URL from
whatever URL it is handed, swapping the async driver for psycopg so a single
`DATABASE_URL` (asyncpg-shaped, what the app uses) is the one source of truth.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

_MIGRATIONS = Path(__file__).resolve().parent / "migrations"


def sync_url_for(url: str) -> str:
    """The synchronous (psycopg) URL Alembic uses, derived from the application
    URL (which is asyncpg — see `nora.infra.sql.engine.create_sql_engine`).

    An asyncpg driver is swapped for psycopg; a URL that already names a sync
    driver, or a bare `postgresql://`, is normalised to `postgresql+psycopg`.
    """
    parsed = make_url(url)
    return parsed.set(drivername="postgresql+psycopg").render_as_string(
        hide_password=False
    )


def alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", sync_url_for(url))
    return cfg


def head_revision() -> str:
    return ScriptDirectory(str(_MIGRATIONS)).get_current_head() or ""


def current_revision(url: str) -> str | None:
    engine = create_engine(sync_url_for(url))
    try:
        with engine.connect() as conn:
            return MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()


def run_migrations(url: str) -> None:
    """`alembic upgrade head` against `url`."""
    command.upgrade(alembic_config(url), "head")


def stamp_head(url: str) -> None:
    """Mark an already-correct database as being at head without running any
    migration. Used once, on the live 0-row database at cutover (design/20 §5)."""
    command.stamp(alembic_config(url), "head")
