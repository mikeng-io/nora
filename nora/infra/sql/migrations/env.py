"""Alembic environment.

Two deliberate deviations from the generated template:

- `include_object` excludes `idx_messages_conv_ts`. The index is `ts DESC` in
  production, declared with `sa.text("ts DESC")` in the metadata. Reflection
  returns a plain column, autogenerate compares the two as different, and the
  result is a `remove_index`/`add_index` pair on EVERY check, forever —
  verified 2026-08-05. Excluding it is the only way `alembic check` is usable
  at all. The ordering it can no longer see is asserted by
  `tests/infra/test_schema_parity.py` via `PRAGMA index_xinfo`.
- `render_as_batch=True`: SQLite cannot ALTER most things in place, so any
  future column change needs Alembic's batch table-rebuild mode.

This env runs SYNCHRONOUSLY, against a `sqlite://` URL, even though the
application uses `sqlite+aiosqlite://`. Migrations are startup-time and
single-threaded; an async env would buy nothing and cost an event loop.

The metadata is read as `schema.METADATA` at configure time, NOT bound with a
top-level `from ...schema import METADATA`. Alembic re-execs this file on every
command, but a from-import snapshots the object, so a test that swaps the
module attribute to simulate drift would silently compare against the original
metadata and report no drift — a gate that passes for the wrong reason.
Verified 2026-08-05: with the module-attribute form, the swap is picked up.

`-x db=` is wired deliberately (see `_url()`): `alembic.ini`'s static url used
to point at `data/messages.db` while `-x db=` was accepted but ignored, so a
command intended for a scratch copy silently ran against production. Alembic
creates `alembic_version` as a side effect before most commands report
anything, so it mutates before it complains — verified the hard way,
2026-08-05.
"""

from __future__ import annotations

from typing import Any

from alembic import context
from sqlalchemy import engine_from_config, pool

from nora.infra.sql import schema  # module, not the METADATA object

config = context.config


def include_object(
    obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any
) -> bool:
    # permanent phantom drift on idx_messages_conv_ts — see module docstring
    return not (type_ == "index" and name == "idx_messages_conv_ts")


def _url() -> str:
    """CLI override: `alembic -x db=/path/to/copy.db`.

    Wired deliberately. `alembic.ini`'s static url used to point at
    `data/messages.db`, and `-x` was accepted-but-ignored, so a command
    intended for a scratch copy silently ran against production — Alembic
    creates `alembic_version` as a side effect before most commands report
    anything, so it mutates before it complains.
    """
    override = context.get_x_argument(as_dictionary=True).get("db")
    if override:
        return f"sqlite:///{override}"
    url = config.get_main_option("sqlalchemy.url")
    if url is None:
        raise RuntimeError("alembic Config has no sqlalchemy.url")
    return url


def _configure(**kw: Any) -> None:
    context.configure(
        target_metadata=schema.METADATA,
        include_object=include_object,
        render_as_batch=True,
        compare_type=True,
        compare_server_default=True,
        **kw,
    )


def run_migrations_offline() -> None:
    _configure(url=_url(), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        _configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
