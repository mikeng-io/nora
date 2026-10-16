"""Every migration's revision id fits `alembic_version.version_num`.

A revision id longer than the `varchar(32)` column fails the `UPDATE
alembic_version` with `StringDataRightTruncation`, and at boot the container
would not start. Only a test that actually migrates reaches that. This reads
the ids off the files (as `test_alembic_single_head.py` does) and the width off
Alembic's own version table, so neither is a guess.
"""

from __future__ import annotations

import ast
from pathlib import Path

from alembic.runtime.migration import MigrationContext

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = ROOT / "nora" / "infra" / "sql" / "migrations" / "versions"
ENV = ROOT / "nora" / "infra" / "sql" / "migrations" / "env.py"


def _version_num_width() -> int:
    """The width Alembic creates `version_num` with, under the options our
    `env.py` passes. It passes no `version_table*` option, which the first
    assertion holds, so the default table is the one production gets."""
    assert "version_table" not in ENV.read_text(encoding="utf-8"), (
        "env.py now configures the version table; build the context here with "
        "the same options before trusting this width"
    )
    ctx = MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True})
    length = ctx._version.c.version_num.type.length
    assert isinstance(length, int), length
    return length


def _revision(path: Path) -> str | None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "revision" for t in node.targets
        ) and isinstance(node.value, ast.Constant):
            return str(node.value.value)
    return None


def test_every_revision_id_fits_the_version_column() -> None:
    width = _version_num_width()
    files = sorted(MIGRATIONS_DIR.glob("*.py"))
    ids = {p.name: _revision(p) for p in files}
    assert len([i for i in ids.values() if i]) >= 60, "the walk read too few revisions"
    too_long = {name: rid for name, rid in ids.items() if rid and len(rid) > width}
    assert not too_long, (
        f"alembic_version.version_num is varchar({width}); these revision ids "
        f"do not fit: " + ", ".join(
            f"{name} ({rid!r}, {len(rid)} chars)" for name, rid in too_long.items()
        )
    )
