"""Alembic environment and versions for the `messages` table.

Not a normal Python package in the usual sense: `env.py` is executed by
Alembic's own loader (via `script_location` in `alembic.ini` / the
programmatic `Config` in `migrations_runner.py`), not imported by
application code. `versions/` holds one hand-written baseline revision.

May import: alembic, sqlalchemy, `nora.infra.sql.schema`.
May NOT import: `nora.config`, `nora.obs` (same two contracts as `nora.infra`).
"""
