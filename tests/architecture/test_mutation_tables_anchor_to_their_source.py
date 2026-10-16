"""Every entry of every `scripts/mutations/*.json` table still names real source.

A mutation entry embeds LITERAL source text (`old`) and the sweep
(`scripts/mutate.py`) replaces it with `new` to see whether the named test goes
red. `make mutate` is not part of `make gate`, so when a refactor moves that
text nothing reddens: the entry stops mutating anything, or points at a file
that is gone, and the table keeps reading as evidence for a guard it no longer
measures. An `old` text found zero times, twice, or in a file that has been
deleted is such an entry, and nothing else reports it.

This is the cheap half of the sweep, and the only half that can run in the gate:
no model call, no database, no test is executed. It checks, per entry:

* the five fields exist and are strings, and there are no others (but an
  optional `comment`, which the runner ignores);
* `file` exists, and `old` occurs in it EXACTLY once (the runner's own rule: a
  second occurrence means the mutation lands somewhere else);
* `new` differs from `old` (an identical pair mutates nothing, and the sweep
  would report the entry as a survivor for a reason that is not the test's);
* the test file named by `target` exists (or the Go module directory, for a
  `go:` target).

What it does NOT check, stated because the omission is the whole risk: that the
named test still goes RED on the mutant. That needs the test run, which is what
`make mutate TABLE=...` is for (`nora-prove-fix` step 7); a table that passes
this file is anchored, not proven.

**A failure here means the table is stale, never that this test is wrong.**
Re-anchor the entry to the code as it is now (same intent), point it at the new
home of the behaviour, or delete it and say in the commit that the behaviour is
gone. Never loosen `old` to something that merely matches: an anchor that no
longer contains the guarded clause mutates nothing and the sweep calls it
caught.

Positive control: a synthetic tree with one entry of each failure shape must be
reported, and one good entry must not be, so a regression in the checker
reddens here rather than turning every table silently green.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TABLES = sorted((ROOT / "scripts" / "mutations").glob("*.json"))

_FIELDS = frozenset({"name", "file", "old", "new", "target"})
_OPTIONAL = frozenset({"comment"})  # prose for the reader; the runner ignores it


def _target_path(target: str) -> tuple[str, bool]:
    """`(path, is_go_module_dir)` of what a `target` names.

    A pytest selector is `<path>[::node] [pytest args]`; a Go target is
    `go:<module dir> <go test args>`."""
    if target.startswith("go:"):
        return target.removeprefix("go:").split()[0], True
    return target.split()[0].split("::")[0], False


def anchor_problems(entries: object, *, root: Path) -> list[str]:
    """Why each entry of one parsed table no longer anchors, in table order.

    Empty means every entry anchors. `root` is the repo root, so a test can
    point this at a synthetic tree."""
    if not isinstance(entries, list) or not entries:
        return ["the table is not a non-empty JSON list"]
    problems: list[str] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            problems.append(f"[{index}] is not an object")
            continue
        label = f"[{index}] {str(entry.get('name', '?'))[:70]!r}"
        keys = set(entry)
        if not (_FIELDS <= keys <= _FIELDS | _OPTIONAL) or not all(
            isinstance(entry[k], str) for k in entry
        ):
            problems.append(
                f"{label}: fields are {sorted(entry)}, want {sorted(_FIELDS)} "
                f"(and optionally {sorted(_OPTIONAL)}) as strings"
            )
            continue
        source = root / entry["file"]
        if not source.is_file():
            problems.append(f"{label}: file {entry['file']} does not exist")
        else:
            seen = source.read_text().count(entry["old"]) if entry["old"] else 0
            if seen != 1:
                problems.append(
                    f"{label}: `old` found {seen}x in {entry['file']}, want exactly once"
                )
        if entry["old"] == entry["new"]:
            problems.append(f"{label}: `new` equals `old`, so the mutation changes nothing")
        path, is_go = _target_path(entry["target"])
        target = root / path
        if not (target.is_dir() if is_go else target.is_file()):
            problems.append(f"{label}: target {path} does not exist")
    return problems


def test_there_are_tables_to_check() -> None:
    """Positive control for the parametrization below: an empty glob would make
    every case vanish and the gate would pass having checked nothing."""
    assert len(TABLES) > 50, f"only {len(TABLES)} mutation tables found under scripts/mutations"
    assert sum(len(json.loads(t.read_text())) for t in TABLES) > 1000


@pytest.mark.parametrize("table", TABLES, ids=lambda t: t.name)
def test_every_mutation_entry_still_anchors_to_its_source(table: Path) -> None:
    problems = anchor_problems(json.loads(table.read_text()), root=ROOT)
    assert not problems, (
        f"{table.relative_to(ROOT)} has {len(problems)} stale entr"
        f"{'y' if len(problems) == 1 else 'ies'}; re-anchor, re-point or delete "
        "(see this file's docstring), never loosen:\n  " + "\n  ".join(problems)
    )


def test_the_checker_reports_every_way_an_entry_goes_stale(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "go-mod").mkdir()
    (tmp_path / "src" / "mod.py").write_text("a = 1\nb = 2\nb = 2\n")
    (tmp_path / "tests" / "test_mod.py").write_text("def test_x(): ...\n")

    def entry(**changes: str) -> dict[str, str]:
        base = {
            "name": "n", "file": "src/mod.py", "old": "a = 1", "new": "a = 0",
            "target": "tests/test_mod.py::test_x -k x",
        }
        return {**base, **changes}

    good = [entry(), entry(target="go:go-mod ./... -run TestX")]
    assert anchor_problems(good, root=tmp_path) == []

    broken = {
        "old not found": entry(old="a = 99"),
        "old found twice": entry(old="b = 2"),
        "empty old": entry(old=""),
        "file missing": entry(file="src/gone.py"),
        "new equals old": entry(new="a = 1"),
        "pytest target missing": entry(target="tests/test_gone.py -k x"),
        "go module missing": entry(target="go:no-such-dir ./..."),
        "extra field": {**entry(), "why": "x"},
        "missing field": {k: v for k, v in entry().items() if k != "new"},
    }
    for shape, bad in broken.items():
        assert len(anchor_problems([entry(), bad], root=tmp_path)) == 1, shape
    assert anchor_problems([], root=tmp_path)
    assert anchor_problems({"not": "a list"}, root=tmp_path)
