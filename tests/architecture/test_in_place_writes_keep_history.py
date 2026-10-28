"""Every table the code changes in place keeps a history, or names the step
that will give it one.

`nora.infra.sql.history.HISTORY_TABLES` is the list the migration's trigger
covers. This is what keeps it true when a store grows an UPDATE, an upsert or
a DELETE on a table nobody listed — the write would work and its history
would silently not exist.

Read from source, like the other architecture tests, anywhere under
`nora/` outside the migrations:

- `sa.update(T)` / `sa.delete(T)` / a bare `update(T)` / `delete(T)`, and
  `T.update()` / `T.delete()` on a table constant;
- for every `on_conflict_do_update`, the nearest `insert(T)` above it (the
  upsert's own table; the statement is built a few lines before the clause);
- raw SQL in the source text: `UPDATE t SET`, `DELETE FROM t`, and
  `INSERT INTO t ... DO UPDATE`;
- a call whose table is a VARIABLE (`sa.delete(table)` in a loop) cannot be
  resolved from source, so every such site is pinned in `_VARIABLE_TARGETS`
  with the tables it reaches, and a new one fails until it is pinned.

And from the schema: a foreign key that cascades (ON DELETE CASCADE / SET
NULL / SET DEFAULT) changes its child's rows whenever its parent's are
deleted, with no statement naming the child. If the parent changes in place,
the child must keep history too — `message_edit` under `messages` is the
case that made this a rule.
"""

from __future__ import annotations

import re
from pathlib import Path

from nora.infra.sql import schema
from nora.infra.sql.history import CAPTURE_SETTING, DEFERRED, HISTORY_TABLES, NOT_RESTORED

ROOT = Path(__file__).resolve().parents[2]
_DIRECT = re.compile(
    r"(?:\bsa\.|(?<![.\w]))(?:update|delete)\(\s*([A-Z_]+)\s*\)"
    r"|\b([A-Z_]+)\.(?:update|delete)\("
)
_VARIABLE = re.compile(r"(?:\bsa\.|(?<![.\w]))(?:update|delete)\(\s*([a-z_]\w*)\s*\)")
_RAW = re.compile(
    r'\bUPDATE\s+"?([a-z_]+)"?\s+SET\b'
    r'|\bDELETE\s+FROM\s+"?([a-z_]+)'
    r'|\bINSERT\s+INTO\s+"?([a-z_]+)"?[^;]{0,400}?\bDO\s+UPDATE\b'
)

#: Sites whose table is a variable: path -> the tables the variable can be.
#: `mobility.py`'s place deletion clears the Tier 3 tables in a loop, and
#: `rollups.py` diffs each unit rollup table through one helper.
_VARIABLE_TARGETS: dict[str, frozenset[str]] = {
    "nora/infra/sql/standing_ledger.py": frozenset({
        "standing_decision", "standing_commitment", "standing_thread",
    }),
    "nora/infra/sql/mobility.py": frozenset({"presence_occupancy", "presence_place_rhythm"}),
    # The unit rollups' diffed write (`_diff_apply`), one table at a time.
    "nora/infra/sql/rollups.py": frozenset({
        "rollup_colocation", "rollup_outing", "rollup_daybook", "rollup_tie",
        "rollup_response",
    }),
}
#: How far above an `on_conflict_do_update` its `insert(T)` may be. The
#: widest current reach (`media_objects.py`) is well under this, and
#: `test_every_upsert_finds_its_insert` fails before one outgrows it.
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
        constants = {a or b for a, b in _DIRECT.findall(text)}
        names = {next(g for g in groups if g) for groups in _RAW.findall(text)}
        for name in names & set(schema.METADATA.tables):
            found.setdefault(name, set()).add(str(path.relative_to(ROOT)))
        rel = str(path.relative_to(ROOT))
        if _VARIABLE.search(text):
            for name in _VARIABLE_TARGETS.get(rel, frozenset()):
                found.setdefault(name, set()).add(rel)
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
    # A NOT_RESTORED table serves no as-of read, so its in-place changes need
    # no history: the memory index (0105) is rebuilt from the restored stores.
    uncovered = {
        table: sorted(paths) for table, paths in _in_place_targets().items()
        if table not in HISTORY_TABLES and table not in DEFERRED
        and table not in NOT_RESTORED
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


def test_every_variable_table_write_is_pinned() -> None:
    sites = {
        str(p.relative_to(ROOT)) for p in (ROOT / "nora").rglob("*.py")
        if "migrations" not in p.parts and _VARIABLE.search(p.read_text(encoding="utf-8"))
    }
    assert sites == set(_VARIABLE_TARGETS), (
        "a write whose table is a variable cannot be checked from source: pin "
        f"it in _VARIABLE_TARGETS with the tables it reaches. unpinned: "
        f"{sorted(sites - set(_VARIABLE_TARGETS))}; stale: "
        f"{sorted(set(_VARIABLE_TARGETS) - sites)}"
    )


def test_a_cascading_child_of_an_in_place_parent_keeps_history() -> None:
    changing = set(HISTORY_TABLES) | set(DEFERRED)
    cascades = [
        (fk.parent.table.name, fk.column.table.name)
        for table in schema.METADATA.tables.values()
        for fk in table.foreign_keys
        if (fk.ondelete or "").upper() in {"CASCADE", "SET NULL", "SET DEFAULT"}
    ]
    assert ("message_edit", "messages") in cascades  # positive control
    unkept = sorted(
        (child, parent) for child, parent in cascades
        if parent in changing and child not in changing
    )
    assert not unkept, (
        "these tables' rows change when their parent's are deleted, with no "
        f"statement naming them, and they keep no history: {unkept}"
    )


def test_each_scanned_form_is_recognised() -> None:
    """The patterns, on the forms they exist to catch, so a regex edit that
    stops matching one cannot pass on an empty scan."""
    assert {a or b for a, b in _DIRECT.findall(
        "sa.update(NOTE) update(REMINDERS) MESSAGES.delete() x.update(NOPE)"
    )} == {"NOTE", "REMINDERS", "MESSAGES"}
    raw = 'UPDATE "standing" SET a = 1; DELETE FROM media_objects; ' \
          "INSERT INTO culture (k) VALUES (1) ON CONFLICT (k) DO UPDATE SET k = 2"
    assert {next(g for g in m if g) for m in _RAW.findall(raw)} == {
        "standing", "media_objects", "culture",
    }
    assert _VARIABLE.findall("sa.delete(table) session.delete(url)") == ["table"]
