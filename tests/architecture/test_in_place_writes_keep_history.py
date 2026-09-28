"""Every table the code changes in place keeps a history, or names the step
that will give it one (design/cognition-loop/10-tiers.md §8, step 1b).

`nora.infra.sql.history.HISTORY_TABLES` is the list the migration's trigger
covers. It was read off the code once; this is what keeps it true when a
store grows an UPDATE, an upsert or a DELETE on a table nobody listed — the
write would work and its history would silently not exist.

Read from source, like the other architecture tests: every `sa.update(T)`
and `sa.delete(T)` target anywhere under `nora/`, and for every
`on_conflict_do_update` the nearest `insert(T)` above it (the upsert's own
table; the statement is built a few lines before the conflict clause).
"""

from __future__ import annotations

import re
from pathlib import Path

from nora.infra.sql import schema
from nora.infra.sql.history import CAPTURE_SETTING, DEFERRED, HISTORY_TABLES

ROOT = Path(__file__).resolve().parents[2]
_DIRECT = re.compile(r"\bsa\.(?:update|delete)\(\s*([A-Z_]+)\s*\)")
#: How far above an `on_conflict_do_update` its `insert(T)` may be. The
#: widest in the tree on 2026-09-28 is 25 lines (`media_objects.py`), and
#: `test_every_upsert_finds_its_insert` fails before one outgrows this.
_UPSERT_REACH = 40
_INSERT = re.compile(r"\b(?:pg_insert|pg\.insert|insert)\(\s*([A-Z_]+)\s*\)")


def _table_of(constant: str) -> str | None:
    table = getattr(schema, constant, None)
    return getattr(table, "name", None)


def _in_place_targets() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in (ROOT / "nora").rglob("*.py"):
        if "migrations" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        constants = set(_DIRECT.findall(text))
        lines = text.splitlines()
        for number, line in enumerate(lines):
            if "on_conflict_do_update(" not in line:
                continue
            for back in range(number, max(-1, number - _UPSERT_REACH), -1):
                inserts = _INSERT.findall(lines[back])
                if inserts:
                    constants.add(inserts[-1])
                    break
        for constant in constants:
            name = _table_of(constant)
            if name is not None:
                found.setdefault(name, set()).add(str(path.relative_to(ROOT)))
    return found


def test_every_upsert_finds_its_insert() -> None:
    lost = []
    for path in (ROOT / "nora").rglob("*.py"):
        if "migrations" in path.parts:
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines):
            if "on_conflict_do_update(" in line and not any(
                _INSERT.search(lines[b])
                for b in range(number, max(-1, number - _UPSERT_REACH), -1)
            ):
                lost.append(f"{path.relative_to(ROOT)}:{number + 1}")
    assert not lost, f"an upsert whose table this scan cannot see: {lost}"


def test_the_scan_sees_the_known_in_place_writers() -> None:
    """Positive control: the scan finds writes this module's authors know
    about, so an empty result cannot read as «nothing changes in place»."""
    targets = _in_place_targets()
    assert {"messages", "note", "reminders", "standing", "culture", "events"} <= set(targets)


def test_every_in_place_table_keeps_history_or_names_its_step() -> None:
    uncovered = {
        table: sorted(paths) for table, paths in _in_place_targets().items()
        if table not in HISTORY_TABLES and table not in DEFERRED
    }
    assert not uncovered, (
        "these tables are changed in place and keep no row_history: add them "
        "to nora.infra.sql.history.HISTORY_TABLES with a migration attaching "
        f"the trigger, or to DEFERRED with the step that versions them: {uncovered}"
    )


def test_only_the_snapshot_switches_capture_off() -> None:
    users = sorted(
        str(p.relative_to(ROOT)) for p in (ROOT / "nora").rglob("*.py")
        if "migrations" not in p.parts
        and ("CAPTURE_SETTING" in (t := p.read_text(encoding="utf-8")) or CAPTURE_SETTING in t)
    )
    assert users == ["nora/infra/sql/history.py", "nora/infra/sql/snapshot_at.py"]
