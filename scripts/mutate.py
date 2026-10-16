"""Mutation sweep — prove a fix is guarded before claiming it is done.

`make gate` green says the suite passes. It does not say the suite would have
NOTICED the bug you just fixed. Those are different claims, and conflating them
is what this script exists to stop: revert the exact production line, run the
test that is supposed to guard it, and require RED.

A fix no test guards can be reverted, even together with others, and the full
suite stays byte-identical to baseline; requiring a mutation result per fix
catches those weak tests before a reviewer does.

This is hand-picked mutation testing, deliberately NOT `mutmut`/`cosmic-ray`
over the whole tree: an automated sweep across ~6000 tests is slow, and
equivalent mutants (edits that do not change behaviour) drown the signal. A
table of the lines you claim to have fixed answers the only question being
asked, in minutes.

Usage:

    uv run python scripts/mutate.py scripts/mutations/<name>.json
    make mutate TABLE=scripts/mutations/<name>.json

A table is per-change and disposable — it names exact source text, so it rots
as soon as that code moves. Write one while doing the work, run it before
reporting, keep it beside the branch as the record of what was proven. The
runner is the durable half; the tables are not.

Table format, a JSON list of objects:

    [
      {
        "name":   "the join's query is bounded",
        "file":   "nora/domain/media_join.py",
        "old":    "<exact text as committed, appearing EXACTLY once>",
        "new":    "<that text as it was before the fix>",
        "target": "tests/unit/test_media_join.py -k hanging_store"
      }
    ]

A `target` starting with `go:` is a Go test run instead of a pytest selector,
for the Go services under `deploy/` (the instagram-bridge):

        "target": "go:deploy/instagram-bridge ./cmd/instagram-bridge -run TestX|TestY"

The first word after `go:` is the Go module's directory; the rest are
`go test` arguments (no shell, so a `-run` regex needs no quoting). The same
two rules hold: "collected" counts the `=== RUN` lines, and a run in which ANY
package reports `[build failed]` or `[setup failed]` collects nothing, so a
mutant that does not compile is a BROKEN ENTRY, never a caught one. Counting
`=== RUN` lines alone is not enough for that: with `./...`, the packages that
still compile run their tests, and the one that does not only turns the exit
code red.

Two harness rules are baked in:

* **pytest exit code 5 is «nothing collected», not «failed».** A typo'd `-k`
  selector reported "caught" while testing nothing. The baseline is asserted
  green AND non-empty before the mutation runs, so a selector that matches
  nothing is a BROKEN ENTRY, never a caught defect.
* **A mutant must die from the reverted BEHAVIOUR**, not from a `NameError` in
  the replacement text that some `except` swallows. So the mutant run must also
  collect and run tests, and the restore is re-verified green afterwards.

Exit status is 0 only when every entry was caught.
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# pytest's summary line, e.g. "3 passed", "1 failed, 2 passed".
_COLLECTED = re.compile(r"(\d+) (?:passed|failed|error)")


# `go test -v`'s per-test start line, e.g. "=== RUN   TestX".
_GO_RAN = re.compile(r"^=== RUN\s", re.MULTILINE)


# A package that did not compile, or whose test binary could not be set up.
_GO_BROKEN = re.compile(r"\[(?:build|setup) failed\]")


def go_verdict(returncode: int, output: str) -> tuple[bool, int]:
    """`(green, collected)` from one `go test -v` run's exit code and output.

    Any `[build failed]` / `[setup failed]` is zero collected, whatever else
    ran: a mutant must die from its behaviour, not from failing to compile,
    and one uncompilable package in `./...` must not pass for a red test.
    """
    if _GO_BROKEN.search(output):
        return False, 0
    return returncode == 0, len(_GO_RAN.findall(output))


def run_go(target: str) -> tuple[bool, int]:
    """`(green, collected)` for one `go:<module dir> <go test args>` target."""
    module, *args = target.split()
    proc = subprocess.run(  # noqa: S603 - fixed go argv, repo-local only
        ["go", "test", "-count=1", "-v", *args],  # noqa: S607
        cwd=ROOT / module, capture_output=True, text=True, check=False,
    )
    return go_verdict(proc.returncode, proc.stdout + proc.stderr)


def run(target: str) -> tuple[bool, int]:
    """`(green, collected)` for one pytest selector, or a `go:` target.

    Exit code 5 is «no tests ran» and is reported as zero collected — NOT as a
    red, which is what let a typo'd selector look like a caught mutation.
    """
    if target.startswith("go:"):
        return run_go(target.removeprefix("go:"))
    proc = subprocess.run(  # noqa: S603 - fixed pytest argv, repo-local only
        ["uv", "run", "pytest", "-q", "--no-header", "-p", "no:randomly",  # noqa: S607
         *target.split()],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    if proc.returncode == 5:
        return False, 0
    collected = sum(int(m.group(1)) for m in _COLLECTED.finditer(proc.stdout))
    return proc.returncode == 0, collected


def sweep(entries: list[dict[str, str]]) -> list[str]:
    """Run every entry, returning the names that were NOT caught."""
    bad: list[str] = []
    for entry in entries:
        name = entry["name"]
        target = entry["target"]

        green, collected = run(target)
        if not collected:
            print(f"BROKEN ENTRY  {name}: selector collected nothing")
            bad.append(f"{name} [empty selector]")
            continue
        if not green:
            print(f"BROKEN ENTRY  {name}: baseline is already red")
            bad.append(f"{name} [baseline red]")
            continue

        path = ROOT / entry["file"]
        src = path.read_text()
        seen = src.count(entry["old"])
        if seen != 1:
            print(f"BROKEN ENTRY  {name}: anchor found {seen}x in {entry['file']}")
            bad.append(f"{name} [anchor]")
            continue

        path.write_text(src.replace(entry["old"], entry["new"], 1))
        try:
            mutant_green, mutant_collected = run(target)
        finally:
            path.write_text(src)

        if not mutant_collected:
            print(f"BROKEN ENTRY  {name}: mutant collected nothing (import error?)")
            bad.append(f"{name} [mutant did not run]")
            continue

        print(f"{'GREEN (BAD)' if mutant_green else 'RED  (good)'}  {name}")
        if mutant_green:
            bad.append(name)

        restored, _ = run(target)
        if not restored:
            print(f"  !! restore failed for {name}")
            bad.append(f"{name} [restore]")
    return bad


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    entries = json.loads(pathlib.Path(argv[1]).read_text())
    bad = sweep(entries)
    print()
    print("UNGUARDED / BROKEN:" if bad else "ALL MUTATIONS CAUGHT")
    for name in bad:
        print(" -", name)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
