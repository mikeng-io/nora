"""A decision row's enforcing test must exist, or the row must say it doesn't.

`design/09-decision-index.md`'s last column names the test that enforces each
decision. That column is the whole point of the index — `AGENTS.md` sends you
here to answer "which test guards this?" — and until 2026-09-08 nothing checked
that the named test was real.

Five rows were citing test files that do not exist:

- **DEC-148 / R-85** named `tests/test_attribution.py` and a predicate module
  `nora/agent/attribution.py`. Both were **deleted** in `6f05181e` ("remove the
  R-85 misattribution guard after a live false refusal") — the decision was
  reversed by evidence, correctly, and the row was left claiming enforcement it
  no longer had. The row also claimed registration in
  `tests/test_acceptance_rules.py::RULE_TESTS`, where it is absent.
- **R-34, DEC-57, DEC-62, DEC-69** each named `tests/test_search.py`, deleted in
  `d3a2b4a7` when the Exa adapter landed; those assertions live in
  `tests/contract/test_search_provider_contract.py` now.

`RULE_TESTS`'s own gate could not catch any of this: it asserts that every entry
IN the registry collects, never that a rule claiming a test HAS one. A row can
therefore name a test that was deleted years of commits ago and stay green — the
decision-index form of "a declared metric with no call site is a lie".

**Rows that legitimately cite deleted tests are marked, not exempted by name.**
The knowledge tier and the language tier were deprecated 2026-09-07 with their
code deleted (issue #115); eleven rows cite their tests and every one carries a
`DEPRECATED` banner. That marking is what this test reads — a banner is a
deliberate statement that the row is a record rather than a live guarantee,
which is exactly the distinction the index's own deprecation convention makes.
So the check is not "the test exists" but "the test exists OR the row says it
does not", and adding a banner to silence a real regression would be a visible,
reviewable edit rather than a quiet one.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "design" / "09-decision-index.md"

#: A row carrying one of these is asserting that it does NOT describe a live
#: guarantee. `DEPRECATED` is the index's own established convention (rows are
#: marked, never renumbered and never removed); the other two were added by the
#: 2026-09-08 audit for a decision reversed by evidence and for one whose stated
#: enforcement mechanism never existed.
_UNENFORCED_MARKERS = ("DEPRECATED", "NOT ENFORCED", "REVERSED")

#: Matches a backticked path into `tests/`, stopping at the first character that
#: cannot appear in one. Deliberately narrow: the column also carries `::node`
#: selectors and prose, and over-matching here would produce the noisy gate this
#: repo already learned to distrust (the R-29 duplicate that sat 15 days because
#: the check cried wolf seven times).
_TEST_PATH = re.compile(r"`(tests/[A-Za-z0-9_/]+\.py)")

_ROW_ID = re.compile(r"\|\s*((?:DEC|R|D|A)-?[0-9]+[a-z]?)\s*\|")


def _rows() -> list[tuple[str, int, str]]:
    """Every table row of the index, as (identifier, line number, text)."""
    rows = []
    for number, line in enumerate(INDEX.read_text().splitlines(), start=1):
        if not line.startswith("|"):
            continue
        match = _ROW_ID.match(line)
        rows.append((match.group(1) if match else "?", number, line))
    return rows


def _unmarked_rows_citing_a_missing_test() -> list[str]:
    """Rows naming a test that is not on disk, without saying the guarantee is gone."""
    found = []
    for identifier, number, line in _rows():
        missing = sorted(
            {path for path in _TEST_PATH.findall(line) if not (ROOT / path).exists()}
        )
        # «DEPRECATED IN PART» retires ONE clause; the rest of the row still
        # stands and still names its enforcing tests, so it must not excuse the
        # whole row the way a bare DEPRECATED does (DEC-197 review: two live
        # rows gained the prefix and silently left this gate).
        unexcused = line.replace("DEPRECATED IN PART", "")
        if missing and not any(marker in unexcused for marker in _UNENFORCED_MARKERS):
            found.append(f"{identifier} (line {number}): {', '.join(missing)}")
    return found


def test_every_row_naming_an_enforcing_test_either_has_it_or_says_it_does_not() -> None:
    unmarked = _unmarked_rows_citing_a_missing_test()
    assert not unmarked, (
        "decision-index rows name an enforcing test that does not exist, and do "
        "not say so. Either repoint the row at where the test actually lives, or "
        "mark it DEPRECATED/REVERSED/NOT ENFORCED if the guarantee is genuinely "
        "gone:\n  " + "\n  ".join(unmarked)
    )


def test_the_index_still_cites_tests_at_all() -> None:
    """Positive control for the test above.

    `test_every_row_naming_an_enforcing_test...` passes trivially if the regex
    stops matching — a column reformat, a renamed directory, an index that no
    longer names tests at all. This asserts the scan still sees a large body of
    citations, so a zero-finding result means "nothing is broken" rather than
    "nothing was looked at".
    """
    cited = {path for _, _, line in _rows() for path in _TEST_PATH.findall(line)}
    assert len(cited) > 150, (
        f"only {len(cited)} test citations found in the decision index — the "
        "matcher has probably stopped seeing the column it is meant to read"
    )


def test_a_row_citing_a_deleted_test_is_caught() -> None:
    """The detector fires on a genuine regression."""
    row = (
        "| DEC-999 | A decision nobody enforces. | `design/00-overview.md` | "
        "`tests/test_this_file_was_deleted_long_ago.py` |"
    )
    missing = [path for path in _TEST_PATH.findall(row) if not (ROOT / path).exists()]
    assert missing == ["tests/test_this_file_was_deleted_long_ago.py"]
    assert not any(marker in row for marker in _UNENFORCED_MARKERS)


def test_a_marked_row_citing_a_deleted_test_is_allowed() -> None:
    """The deprecation convention is honoured rather than special-cased by name.

    This is the eleven knowledge/language-tier rows' shape: the test is genuinely
    gone, and the row says so.
    """
    row = (
        "| DEC-998 | **DEPRECATED 2026-09-07 — the tier this decides was deleted "
        "(issue #115).** | `design/00-overview.md` | `tests/infra/test_fact_schema.py` |"
    )
    missing = [path for path in _TEST_PATH.findall(row) if not (ROOT / path).exists()]
    assert missing, "the fixture must cite a genuinely absent test to be meaningful"
    assert any(marker in row for marker in _UNENFORCED_MARKERS)


def test_a_sub_lettered_row_is_read_as_its_own_identifier() -> None:
    """`R-6b` is not `R-6`.

    The same false positive that made the duplicate-number check untrustworthy
    would misattribute a finding here to the wrong rule, which is worse than
    useless in a message a reader is meant to act on.
    """
    row = "| R-6b | Something. | `design/00-overview.md` | `tests/test_guards.py` |"
    match = _ROW_ID.match(row)
    assert match is not None
    assert match.group(1) == "R-6b"


def test_a_row_deprecated_only_in_part_is_still_held_to_its_tests() -> None:
    """The partial marker must not switch the row off: the clauses that stand
    are still enforced by the tests the row names."""
    row = (
        "| DEC-999 | **DEPRECATED IN PART 2026-09-24 — clause (3) superseded.** "
        "The rest stands. | `tests/test_this_file_was_deleted_long_ago.py` |"
    )
    unexcused = row.replace("DEPRECATED IN PART", "")
    assert not any(marker in unexcused for marker in _UNENFORCED_MARKERS)
