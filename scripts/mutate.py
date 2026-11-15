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

    python scripts/mutate.py scripts/mutations/<name>.json
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
two rules hold: "ran" counts the `=== RUN` lines, and a run in which ANY
package reports `[build failed]` or `[setup failed]` ran nothing, so a
mutant that does not compile is a BROKEN ENTRY, never a caught one. Counting
`=== RUN` lines alone is not enough for that: with `./...`, the packages that
still compile run their tests, and the one that does not only turns the exit
code red.

Python phases run ONE command: this interpreter's `scripts.pytest_gate`, which
owns the shared host lease itself — the sweep never touches the lock, a
generation token, or the gate's environment. A held lease is waited out, never
read as a verdict.

Harness rules, baked into the verdict:

* **pytest exit code 5 is «nothing collected», not «failed»**, and a run with
  no `failed` test is not red — an import, collection or setup error kills a
  mutant without proving the reverted behaviour was noticed. Only `rc=1` with
  a real failure count and NO error count is RED: a failed test beside any
  setup or teardown error is contaminated proof, never a catch. A busy lease —
  the gate's own busy/stale-lease signal, never the bare exit code — is waited
  out then reported UNAVAILABLE; a pytest error run, including a collection
  exit 2, is BROKEN; every other nonzero exit is an error, never a catch.
* **A mutant must die from the reverted BEHAVIOUR.** The mutant run must
  actually execute tests, and the restore is re-verified green afterwards,
  with the source's sha256 and git diff read back for byte-exact restoration.
* Every phase writes its full stdout/stderr, command, exit code and counts to
  a private proof directory outside git (dirs 0700, files 0600); each phase
  receipt binds its actual HEAD and the mutated file's sha256, and
  `manifest.json` records HEAD, the table's hash, each entry's anchor count
  and restoration evidence.

`ALL MUTATIONS RED AND RESTORED` prints only when every selected entry went
RED and byte-restored green — observed failures, not proof; confirm each
failure is its intended assertion. Exit status is 0 only then.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROOFS = pathlib.Path.home() / ".local/share/nora/mutation-proofs"

# pytest's summary counts, e.g. "3 passed", "1 failed, 2 passed", "1 error".
_PASSED = re.compile(r"(\d+) passed")
_FAILED = re.compile(r"(\d+) failed")
_ERRORS = re.compile(r"(\d+) errors?")

# `go test -v`'s per-test start line, e.g. "=== RUN   TestX".
_GO_RAN = re.compile(r"^=== RUN\s", re.MULTILINE)

# A package that did not compile, or whose test binary could not be set up.
_GO_BROKEN = re.compile(r"\[(?:build|setup) failed\]")

# The gate's non-verdict outcomes (scripts/pytest_gate.py's own wording): the
# shared host lease is held, or a carried generation went stale mid-run.
_BUSY = re.compile(r"Nora test host busy|Nora test lease changed")

LEASE_RETRIES = 240
LEASE_WAIT_S = 15

# The one "caught" disposition: only what the phases observed, never a claim
# of semantic proof — the failing assertion still needs a human read.
CAUGHT = ("baseline green, mutant RED, byte-restored green — "
          "confirm the failure is the intended assertion")


@dataclass(frozen=True)
class Phase:
    """One test run's receipt: what ran, how it ended, what it established."""

    command: list[str]
    rc: int
    waits: int
    verdict: str  # green | red | empty | errored | busy | error
    passed: int
    failed: int
    errors: int
    log: str | None

    @property
    def ran(self) -> int:
        return self.passed + self.failed

    def record(self) -> dict[str, Any]:
        return {
            "command": " ".join(self.command), "rc": self.rc,
            "lease_waits": self.waits, "verdict": self.verdict,
            "passed": self.passed, "failed": self.failed,
            "errors": self.errors, "log": self.log,
        }


def _count(pattern: re.Pattern[str], out: str) -> int:
    return sum(int(m.group(1)) for m in pattern.finditer(out))


def pytest_verdict(returncode: int, output: str) -> str:
    """One pytest_gate run's verdict; only rc 1 with real failures is red.

    Red also requires NO error count: a failed test beside any setup or
    teardown error is contaminated proof, not a catch. Busy is the gate's
    own busy/stale-lease signal, never the bare exit code — a collection
    exit 2 without that signal is an error run, not contention.
    """
    if _BUSY.search(output):
        return "busy"
    if returncode == 0:
        return "green"
    if returncode == 5:
        return "empty"
    if _ERRORS.search(output):
        return "errored"
    if returncode == 1:
        return "red" if _FAILED.search(output) else "error"
    return "error"


def go_verdict(returncode: int, output: str) -> tuple[bool, int]:
    """`(green, collected)` from one `go test -v` run's exit code and output.

    Any `[build failed]` / `[setup failed]` is zero collected, whatever else
    ran: a mutant must die from its behaviour, not from failing to compile,
    and one uncompilable package in `./...` must not pass for a red test.
    """
    if _GO_BROKEN.search(output):
        return False, 0
    return returncode == 0, len(_GO_RAN.findall(output))


def _gate_command(target: str) -> list[str]:
    """The one Python test command: this interpreter, the authorized gate."""
    return [sys.executable, "-m", "scripts.pytest_gate", "--workers", "0",
            *target.split()]


def _private_dir(path: pathlib.Path) -> None:
    """A retained receipt directory no one else may read (0700)."""
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)


def _write_private(path: pathlib.Path, text: str) -> None:
    """A retained receipt file no one else may read (0600)."""
    path.write_text(text)
    path.chmod(0o600)


def _write_log(log: pathlib.Path | None, command: list[str], waits: int,
               proc: subprocess.CompletedProcess[str]) -> str | None:
    if log is None:
        return None
    _private_dir(log.parent)
    _write_private(log, (
        f"$ {' '.join(command)}\nrc={proc.returncode} lease_waits={waits}\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}\n"))
    return str(log)


def _phase(command: list[str], log: pathlib.Path | None) -> Phase:
    """Run one command to a verdict, waiting out a held host lease."""
    waits = 0
    while True:
        try:
            proc = subprocess.run(  # noqa: S603 — fixed argv, repo-local only
                command, cwd=ROOT, capture_output=True, text=True, check=False)
        except OSError as exc:
            if log is not None:
                _private_dir(log.parent)
                _write_private(log, f"$ {' '.join(command)}\n{exc}\n")
            return Phase(command=command, rc=-1, waits=waits, verdict="error",
                         passed=0, failed=0, errors=0,
                         log=str(log) if log is not None else None)
        verdict = pytest_verdict(proc.returncode, proc.stdout + proc.stderr)
        if verdict != "busy" or waits >= LEASE_RETRIES:
            break
        waits += 1
        time.sleep(LEASE_WAIT_S)
    return Phase(
        command=command, rc=proc.returncode, waits=waits, verdict=verdict,
        passed=_count(_PASSED, proc.stdout), failed=_count(_FAILED, proc.stdout),
        errors=_count(_ERRORS, proc.stdout),
        log=_write_log(log, command, waits, proc))


def run(target: str, log: pathlib.Path | None = None) -> Phase:
    """One phase for a pytest selector, or a `go:` target."""
    if target.startswith("go:"):
        module, *args = target.removeprefix("go:").split()
        command = ["go", "test", "-count=1", "-v", *args]
        try:
            proc = subprocess.run(  # noqa: S603 — fixed go argv, repo-local only
                command, cwd=ROOT / module, capture_output=True, text=True,
                check=False)
        except OSError as exc:
            if log is not None:
                _private_dir(log.parent)
                _write_private(log, f"$ {' '.join(command)}\n{exc}\n")
            return Phase(command=command, rc=-1, waits=0, verdict="error",
                         passed=0, failed=0, errors=0,
                         log=str(log) if log is not None else None)
        out = proc.stdout + proc.stderr
        green, collected = go_verdict(proc.returncode, out)
        return Phase(
            command=command, rc=proc.returncode, waits=0,
            verdict="green" if green else ("empty" if not collected else "red"),
            passed=collected if green else 0,
            failed=0 if green or not collected else collected,
            errors=0, log=_write_log(log, command, 0, proc))
    return _phase(_gate_command(target), log)


def _git(*args: str) -> str:
    return subprocess.run(  # noqa: S603 — fixed git argv, repo-local only
        ["git", *args], cwd=ROOT, capture_output=True, text=True,  # noqa: S607
        check=True).stdout


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def prove(entry: dict[str, str], tag: str, logdir: pathlib.Path) -> dict[str, Any]:
    """Baseline → damaged → restored for one table entry, with custody receipts."""
    path = ROOT / entry["file"]
    rec: dict[str, Any] = {
        "name": entry["name"], "file": entry["file"], "target": entry["target"],
        "head_at_start": _git("rev-parse", "HEAD").strip(),
    }

    def phase(name: str) -> Phase:
        result = run(entry["target"], logdir / f"{tag}-{name}.log")
        rec[name] = {
            "head": _git("rev-parse", "HEAD").strip(),
            "source_sha256": (
                _sha256(path.read_bytes()) if path.exists() else None),
            **result.record(),
        }
        return result

    baseline = phase("baseline")
    if baseline.verdict == "busy":
        rec["disposition"] = "UNAVAILABLE: test host lease never freed"
        return rec
    if baseline.verdict == "error":
        rec["disposition"] = f"UNAVAILABLE: baseline phase exited rc={baseline.rc}"
        return rec
    if baseline.ran == 0:
        rec["disposition"] = "BROKEN ENTRY: baseline collected nothing"
        return rec
    if baseline.verdict == "errored":
        rec["disposition"] = "BROKEN ENTRY: baseline errored"
        return rec
    if baseline.verdict != "green":
        rec["disposition"] = "BROKEN ENTRY: baseline is already red"
        return rec

    src = path.read_bytes()
    rec["file_sha256_before"] = _sha256(src)
    rec["git_diff_before"] = _git("diff", "--", entry["file"])
    seen = src.count(entry["old"].encode())
    rec["anchor_count"] = seen
    if seen != 1:
        rec["disposition"] = f"BROKEN ENTRY: anchor found {seen}x in {entry['file']}"
        return rec

    path.write_bytes(src.replace(entry["old"].encode(), entry["new"].encode(), 1))
    try:
        mutant = phase("mutant")
    finally:
        path.write_bytes(src)
    rec["file_sha256_after_restore"] = _sha256(path.read_bytes())
    rec["byte_restored"] = (
        rec["file_sha256_after_restore"] == rec["file_sha256_before"])
    rec["git_diff_after_restore"] = _git("diff", "--", entry["file"])
    rec["restored_to_pre_run_state"] = (
        rec["git_diff_after_restore"] == rec["git_diff_before"])

    if mutant.verdict == "busy":
        rec["disposition"] = "UNAVAILABLE: lease held through the mutant run"
        return rec
    if mutant.verdict == "error":
        rec["disposition"] = f"UNAVAILABLE: mutant phase exited rc={mutant.rc}"
        return rec

    restored = phase("restored")
    if mutant.verdict == "errored":
        rec["disposition"] = (
            "BROKEN ENTRY: mutant errored — a setup/teardown/collection "
            "error is not proof")
    elif mutant.ran == 0:
        rec["disposition"] = "BROKEN ENTRY: mutant collected nothing (import error?)"
    elif mutant.verdict == "green":
        rec["disposition"] = "SURVIVED: mutant stayed green"
    elif restored.verdict == "busy":
        rec["disposition"] = "UNAVAILABLE: lease held through the restore run"
    elif (restored.verdict != "green" or not rec["byte_restored"]
            or not rec["restored_to_pre_run_state"]):
        rec["disposition"] = "RED but restoration did not verify"
    else:
        rec["disposition"] = CAUGHT
    return rec


def sweep(table: pathlib.Path, proof_dir: pathlib.Path | None = None,
          *, now: str | None = None) -> tuple[list[str], pathlib.Path]:
    """Run every entry of a table; return the names that were NOT caught."""
    head = _git("rev-parse", "HEAD").strip()
    entries = json.loads(table.read_text())
    proof_dir = proof_dir or (
        PROOFS / f"{table.stem}-{head[:12]}-{now or time.strftime('%Y%m%d-%H%M%S')}")
    _private_dir(proof_dir)

    bad: list[str] = []
    results: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        name = entry["name"]
        rec = prove(entry, f"{index:02d}", proof_dir)
        rec["index"] = index
        results.append(rec)
        disposition = rec["disposition"]
        print(f"{disposition:60s} {name}", flush=True)
        if disposition != CAUGHT:
            bad.append(f"{name} [{disposition}]")

    _write_private(proof_dir / "manifest.json", json.dumps({
        "head": head, "table": str(table.resolve()),
        "table_sha256": _sha256(table.read_bytes()), "results": results,
    }, indent=2) + "\n")
    _write_private(proof_dir / "final-git-status.txt",
                   _git("status", "--porcelain"))
    print(f"receipts: {proof_dir}")
    return bad, proof_dir


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Prove each fix in a mutation table is guarded.")
    parser.add_argument("table", type=pathlib.Path)
    parser.add_argument("--proof-dir", type=pathlib.Path,
                        help="receipt directory (default: a fresh private dir)")
    args = parser.parse_args(argv[1:])
    bad, _ = sweep(args.table, args.proof_dir)
    print()
    if bad:
        print("UNGUARDED / BROKEN:")
        for name in bad:
            print(" -", name)
    else:
        print("ALL MUTATIONS RED AND RESTORED")
        print("each RED is an observed test failure — "
              "confirm it is the intended assertion")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
