"""A DEC/R/D/A number in `design/09-decision-index.md` is unique across the whole table.

AGENTS.md's "Never" list: a number is never taken from a chat agreement, it is
re-read from the file at MERGE time, because two sessions can independently pick
the same number and land it without a git conflict (two different lines, no
textual overlap). AGENTS.md carries the hand-run check:

    grep -oE '^\\| R-[0-9]+ \\|' design/09-decision-index.md | grep -oE '[0-9]+' |
    sort -n | uniq -d

The trailing `\\|` in that command is load-bearing: it requires the pipe to
close the cell immediately after the digits, which a sub-lettered id such as
`R-6b` never satisfies (its cell is `R-6b`, not `R-6`). Without it the digits of
`R-6b` read as `6` and collide with the real `R-6` row.

This file is that check, executable: parsed off the table rows rather than
shelled out to `grep`, so it runs under `pytest -q` with everything else in
`make gate`, and it matches the SAME cell boundary (whole ID cell, not a digit
substring) so `R-6b` and `R-6` are never confused in either direction.

Positive control: a synthetic table with a genuine duplicate (two rows both
`R-29`) must be caught, and a synthetic table with `R-6` and `R-6b` as
DIFFERENT rows must NOT be flagged. Both are asserted below, so a regression in
the matcher shows up as a missed catch or a false alarm rather than as a
silently green diff.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DECISION_INDEX = ROOT / "design" / "09-decision-index.md"

# Whole-cell match: the pipe must close the cell immediately after the ID, so
# `R-6b` (a real, distinct row) can never be misread as `R-6`. This is the
# executable form of AGENTS.md's own `grep -oE '^\| R-[0-9]+ \|'` — generalized
# to the four namespaces (D*, A*, R-*, DEC-*) design/09 actually carries.
_ROW_ID = re.compile(r"^\|\s*([A-Za-z]+-?\d+[a-z]?)\s*\|")


def row_ids(markdown: str) -> list[str]:
    """Every decision-table-row ID cell, in file order, exactly as written —
    `R-6` and `R-6b` are different strings and stay different strings."""
    return [m.group(1) for line in markdown.splitlines() if (m := _ROW_ID.match(line))]


def duplicate_ids(ids: list[str]) -> list[str]:
    """IDs whose exact string appears on more than one row, sorted."""
    counts = Counter(ids)
    return sorted(id_ for id_, n in counts.items() if n > 1)


def test_no_duplicate_ids_in_the_decision_index() -> None:
    markdown = DECISION_INDEX.read_text(encoding="utf-8")
    ids = row_ids(markdown)

    # Positive control on the real file: an empty duplicate list must mean "we
    # read the table and found no dup", not "the row pattern stopped matching
    # the decision index's own formatting".
    assert len(ids) >= 200, (
        f"only matched {len(ids)} decision rows in {DECISION_INDEX.relative_to(ROOT)} "
        "— the row pattern likely stopped matching the table's formatting; fix "
        "the walk before trusting a green duplicate check"
    )

    dupes = duplicate_ids(ids)
    assert not dupes, (
        "duplicate DEC/R/D/A id(s) in design/09-decision-index.md (AGENTS.md's "
        "'Never take a DEC or R number from a chat agreement' — the R-29 "
        f"incident, 15 days on master): {dupes}"
    )


def test_a_genuine_duplicate_is_caught() -> None:
    """Positive control: inject a real duplicate (two rows both `R-29`) into a
    synthetic table and confirm the detector actually flags it."""
    synthetic = "\n".join(
        [
            "| ID | Statement | Enforcing test |",
            "|---|---|---|",
            "| R-27 | Some rule. | `test_a` |",
            "| R-28 | Another rule. | `test_b` |",
            "| R-29 | A rule someone else already numbered R-29. | `test_c` |",
            "| R-29 | The genuine duplicate, landed independently. | `test_d` |",
        ]
    )
    assert duplicate_ids(row_ids(synthetic)) == ["R-29"]


def test_sub_lettered_rows_are_not_confused_with_their_parent() -> None:
    """`R-6` and `R-6b` (and `R-10` / `R-10c`) are DIFFERENT ids and must never
    collide, even though a digit-only extraction would read `6` out of both
    `R-6` and `R-6b`."""
    synthetic = "\n".join(
        [
            "| ID | Statement | Enforcing test |",
            "|---|---|---|",
            "| R-6 | The parent rule. | `test_leak_guard_blocks_canary` |",
            "| R-6b | ...and a verbatim doctrine span specifically. | `test_b` |",
            "| R-6c | A normal reply is never blocked. | `test_c` |",
            "| R-10 | The parent rule. | `test_lock` |",
            "| R-10b | ...released on timeout. | `test_timeout` |",
            "| R-10c | ...released on an LLM error. | `test_llm_error` |",
        ]
    )
    ids = row_ids(synthetic)
    assert ids == ["R-6", "R-6b", "R-6c", "R-10", "R-10b", "R-10c"]
    assert duplicate_ids(ids) == []
