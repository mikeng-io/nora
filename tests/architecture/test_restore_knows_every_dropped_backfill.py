"""Every column a migration gave existing rows is one the restore knows.

Two shapes, both found by scanning the migrations:

- a NOT NULL column added with a `server_default` the migration then
  dropped (`alter_column(..., server_default=None)`), leaving the target
  column with nothing to fill a missing key from (e.g. `compute_jobs.kind`,
  added by 0094); and
- a column added with no default at all whose value a backfill wrote for
  every existing row (`add_column` + `UPDATE`, e.g. `due_tick`, 0150) —
  the same hole: a `row_history` image recorded before the migration has
  no key for it, so the as-of restore
  (`nora.infra.sql.snapshot_at._insert_sql`) must coalesce it from the
  image's own fields or the row restores NULL — refused for a NOT NULL
  column, silently wrong for a deadline.

History began at 0068 (`0068_system_time_history`); a column dropped earlier
is in every image, so only later migrations matter.
"""

from __future__ import annotations

import re
from pathlib import Path

from nora.infra.sql.snapshot_at import BACKFILLED, copied_tables

ROOT = Path(__file__).resolve().parents[2]
VERSIONS = ROOT / "nora" / "infra" / "sql" / "migrations" / "versions"
HISTORY_BEGAN = 68

#: An `alter_column(` call whose arguments drop the default, wherever
#: `server_default=None` sits among them; its first two quoted strings, in
#: either quote style, are the table and the column.
_DROPPED = re.compile(r"alter_column\((?P<args>[^)]*?server_default\s*=\s*None)", re.S)
#: An `add_column("table", sa.Column("column"` call — a column an image
#: predating the migration cannot carry; the capture runs through the
#: column's quoted name, so the first two quoted strings are the table and
#: the column.
_ADDED = re.compile(
    r"""add_column\((?P<args>[^)]*?sa\.Column\(\s*["'][a-z0-9_]+["'])""", re.S)
_NAMES = re.compile(r"""["'](?P<name>[a-z0-9_]+)["']""")


def _dropped_backfills() -> set[tuple[str, str]]:
    copied = set(copied_tables())
    found: set[tuple[str, str]] = set()
    for path in sorted(VERSIONS.glob("[0-9][0-9][0-9][0-9]_*.py")):
        if int(path.name[:4]) <= HISTORY_BEGAN:
            continue
        for m in _DROPPED.finditer(path.read_text()):
            names = [n["name"] for n in _NAMES.finditer(m["args"])][:2]
            if len(names) == 2 and names[0] in copied:
                found.add((names[0], names[1]))
    return found


def _added_columns() -> set[tuple[str, str]]:
    copied = set(copied_tables())
    found: set[tuple[str, str]] = set()
    for path in sorted(VERSIONS.glob("[0-9][0-9][0-9][0-9]_*.py")):
        if int(path.name[:4]) <= HISTORY_BEGAN:
            continue
        for m in _ADDED.finditer(path.read_text()):
            names = [n["name"] for n in _NAMES.finditer(m["args"])][:2]
            if len(names) == 2 and names[0] in copied:
                found.add((names[0], names[1]))
    return found


def test_the_pattern_reads_the_formatting_variants() -> None:
    for text in (
        'op.alter_column("t", "c", server_default=None)',
        "op.alter_column('t', 'c', existing_type=sa.Text, server_default = None)",
        'op.alter_column(\n    "t",\n    "c",\n    server_default=None,\n)',
    ):
        m = _DROPPED.search(text)
        assert m is not None, text
        assert [n["name"] for n in _NAMES.finditer(m["args"])][:2] == ["t", "c"], text


def test_the_scan_finds_the_known_cases() -> None:
    # A scan that matched nothing would pass the tests below vacuously.
    assert ("compute_jobs", "kind") in _dropped_backfills()
    assert ("stimulus", "due_tick") in _added_columns()


def test_every_dropped_backfill_on_a_copied_table_is_restored() -> None:
    missing = _dropped_backfills() - set(BACKFILLED)
    assert not missing, (
        f"{sorted(missing)}: a migration after 0068 dropped the backfill default of a "
        "NOT NULL column on a table the as-of restore copies. Add the value it "
        "backfilled to `nora.infra.sql.snapshot_at.BACKFILLED`, or every image "
        "recorded before it restores that column as NULL and the table is refused."
    )


def test_no_backfill_entry_outlives_its_migration() -> None:
    stale = set(BACKFILLED) - (_dropped_backfills() | _added_columns())
    assert not stale, (
        f"{sorted(stale)} name no migration that dropped a backfill default "
        "or added the column on a copied table"
    )
