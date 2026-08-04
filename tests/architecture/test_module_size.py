"""Spec §10: "Module size: soft cap 200 lines, hard cap 300 — calibrated on
this repo's own evidence (`handler.py` went wrong at 229 with 8
responsibilities; healthy modules are 50-120). One stage = one module = one
public `run()`."

This is the hard-cap half of that rule. There is no soft-cap test: 200 is a
"look twice" heuristic for a human doing a review, not a bright line a CI gate
can enforce without false positives on legitimately dense modules (e.g.
`nora/domain/ports.py`, all Protocol definitions with no behaviour to split
out). 300 is the line past which the spec's own calibration example
(`handler.py`, 229 lines, 8 responsibilities) says something has gone wrong,
so that is what is mechanically enforced.

**Scope: `nora/` production modules only, not `tests/`.** Three test files
currently exceed 300 lines (`tests/integration/harness.py`,
`tests/unit/test_engagement_pipeline.py`, `tests/infra/test_audit_store.py`).
This is a deliberate scoping decision, not an oversight discovered and left
unfixed: the spec's rationale is about a module's *responsibilities* growing
unbounded — "one stage, one module, one public entry point" — and its own
calibration example, `handler.py`, is production code. A test file's length
tracks the number of *cases* a behaviour needs covered, not the number of
responsibilities a module owns; splitting a test file to satisfy a line-count
gate, with no corresponding change in what it's responsible for, is exactly
the kind of mechanical-gate-over-actual-signal split CLAUDE.md's module-size
rule exists to avoid producing. Extending this gate to `tests/` was
considered and rejected for this task — see `task-11-brief.md`.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HARD_CAP = 300


def _production_modules() -> list[Path]:
    return sorted((ROOT / "nora").rglob("*.py"))


def test_no_production_module_exceeds_the_hard_cap():
    offenders = {}
    for path in _production_modules():
        n = sum(1 for _ in path.open(encoding="utf-8"))
        if n > HARD_CAP:
            offenders[str(path.relative_to(ROOT))] = n
    assert not offenders, (
        f"module(s) over the {HARD_CAP}-line hard cap: {offenders}"
    )
