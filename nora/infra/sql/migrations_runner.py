"""Programmatic Alembic. Runtime never reads `alembic.ini`.

`alembic.ini` exists at the repo root for CLI use (`uv run alembic check`,
`uv run alembic revision --autogenerate`). The application builds its Config
here instead, so a missing or unmounted ini can never stop Nora from starting.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

_MIGRATIONS = Path(__file__).resolve().parent / "migrations"


def sync_url_for(db_path: Path | str) -> str:
    """The synchronous URL Alembic uses. The application itself uses the
    aiosqlite variant — see `nora.infra.sql.engine.async_url_for`."""
    return f"sqlite:///{Path(db_path)}"


def alembic_config(db_path: Path | str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", sync_url_for(db_path))
    return cfg


def head_revision() -> str:
    return ScriptDirectory(str(_MIGRATIONS)).get_current_head() or ""


def current_revision(db_path: Path | str) -> str | None:
    engine = create_engine(sync_url_for(db_path))
    try:
        with engine.connect() as conn:
            return MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()


def run_migrations(db_path: Path | str) -> None:
    """`alembic upgrade head`. Creates the parent directory if absent."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    command.upgrade(alembic_config(db_path), "head")


def stamp_head(db_path: Path | str) -> None:
    """Mark an already-correct database as being at head without running any
    migration. Used once, on the live 0-row database (Task 10)."""
    command.stamp(alembic_config(db_path), "head")
