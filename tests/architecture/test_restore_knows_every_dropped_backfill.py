"""Every backfill default a migration dropped is one the restore knows.

A migration that adds a NOT NULL column with a `server_default`, then drops
the default (`alter_column(..., server_default=None)`), leaves the target
column with nothing to fill a missing key from. A `row_history` image
recorded before that migration has no key for the column, so the as-of
restore (`nora.infra.sql.snapshot_at._insert_sql`) would insert NULL and the
whole table would be refused. Measured 2026-10-15: every `conversational_recall`
case ERRORed on `compute_jobs.kind`, added by 0094.

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

_DROPPED = re.compile(
    r'alter_column\(\s*"(?P<table>[a-z_]+)"\s*,\s*"(?P<column>[a-z_]+)"\s*,\s*server_default\s*=\s*None',
)


def _dropped_backfills() -> set[tuple[str, str]]:
    copied = set(copied_tables())
    found: set[tuple[str, str]] = set()
    for path in sorted(VERSIONS.glob("[0-9][0-9][0-9][0-9]_*.py")):
        if int(path.name[:4]) <= HISTORY_BEGAN:
            continue
        for m in _DROPPED.finditer(path.read_text()):
            if m["table"] in copied:
                found.add((m["table"], m["column"]))
    return found


def test_the_scan_finds_the_known_case() -> None:
    # A scan that matched nothing would pass the test below vacuously.
    assert ("compute_jobs", "kind") in _dropped_backfills()


def test_every_dropped_backfill_on_a_copied_table_is_restored() -> None:
    missing = _dropped_backfills() - set(BACKFILLED)
    assert not missing, (
        f"{sorted(missing)}: a migration after 0068 dropped the backfill default of a "
        "NOT NULL column on a table the as-of restore copies. Add the value it "
        "backfilled to `nora.infra.sql.snapshot_at.BACKFILLED`, or every image "
        "recorded before it restores that column as NULL and the table is refused."
    )


def test_no_backfill_entry_outlives_its_migration() -> None:
    stale = set(BACKFILLED) - _dropped_backfills()
    assert not stale, f"{sorted(stale)} name no migration that dropped a backfill default"
