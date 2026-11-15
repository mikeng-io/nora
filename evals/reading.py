"""Reading a report: errored rows, sampled draws and their names.

What `read`/`sample_read` measure lives in `evals/backbone.py` — the
`environment_state` channel and the `NORA_*` names a drive writes. This
module only reads the SDK `EvaluationReport` back: an unanswered row is an
errored reading, and a sampled case is `UNSTABLE` unless every draw agreed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from strands_evals import Case

from evals.backbone import ERROR, FAIL, PASS


@dataclass(frozen=True)
class Reading:
    """What a report says once ERROR is taken out of the denominator."""

    passed: int
    failed: int
    errored: int

    @property
    def gradable(self) -> int:
        return self.passed + self.failed

    def __str__(self) -> str:
        return (
            f"{self.passed}/{self.gradable} graded, {self.errored} ERROR"
            if self.gradable
            else f"0/0 graded, {self.errored} ERROR — nothing was measured"
        )


def read(report: Any) -> Reading:
    """The gate's own reader. **Keys on `label`, never on `test_pass`.**

    `label` survives in exactly one place —
    `EvaluationReport.detailed_results`, a `list[list[EvaluationOutput]]`. It is
    NOT on `report.cases` (those rows are the `EvaluationData`), and it is not
    reachable from `report.scores`, `report.test_passes` or
    `report.overall_score`. So the two fields a reader would reach for first are
    precisely the two that cannot tell a broken instrument from a bad answer,
    and `overall_score` averages both kinds of zero in one denominator.

    That is why this function exists rather than a one-line property read.
    """
    counts = {PASS: 0, FAIL: 0, ERROR: 0}
    for outputs in report.detailed_results:
        if not outputs:
            # A (case, evaluator) row whose evaluator produced no verdict —
            # a task or evaluator error — is an errored reading, never a
            # smaller denominator.
            counts[ERROR] += 1
        for out in outputs:
            label = getattr(out, "label", None)
            counts[label if label in counts else ERROR] += 1
    return Reading(passed=counts[PASS], failed=counts[FAIL], errored=counts[ERROR])


#: A fourth answer, alongside `PASS`/`FAIL`/`ERROR` — see `sample_read()`.
UNSTABLE = "UNSTABLE"


def sampled(base: Case, n: int) -> list[Case]:
    """`n` independently-named copies of `base`, for a case whose signal is
    stochastic rather than reading it once.

    A model-dependent case can swap verdicts across runs at `sample: 1`. The
    obvious fix (run the same `Case` N times) does not work as written: `Experiment`
    has no repeat parameter, and `EvaluationDataStore`/the SDK's own caching
    key on `case_name`, so N draws under `base.name` unchanged would
    silently collide rather than produce N rows. Each draw needs a name of
    its own; `sample_read()` undoes the naming to read them back as one.
    """
    return [
        base.model_copy(update={"name": f"{base.name}#{i}", "session_id": f"{base.name}#{i}"})
        for i in range(1, n + 1)
    ]


def base_name(name: str) -> str:
    """The name `sampled()` drew FROM, undoing its `#i` suffix. A name that
    was never run through `sampled()` returns unchanged."""
    head, sep, tail = name.rpartition("#")
    return head if sep and tail.isdigit() else name


@dataclass(frozen=True)
class SampleReading:
    """One base case's verdict across its `sampled()` draws."""

    verdict: str  # PASS | FAIL | ERROR | UNSTABLE
    passed: int
    failed: int
    errored: int


def sample_read(report: Any) -> dict[str, SampleReading]:
    """Group a report's rows by the base name `sampled()` drew them from,
    and read each group's stability rather than picking a winner.

    `report.cases` (a `list[dict]`) and
    `report.detailed_results` (a `list[list[EvaluationOutput]]`) are
    aligned 1:1 — one entry per (case, evaluator) PAIR, not one entry per
    case, which is easy to misread from `detailed_results`'s own docstring
    ("Attributes: cases: ... Each record carries an `evaluator` key").
    `read()`'s flat tally never needs this distinction; grouping draws back
    into their base case does.

    **`UNSTABLE` is a fourth answer, deliberately not a majority vote.** A
    case only reads `PASS` or `FAIL` here when every draw agreed. Collapsing
    disagreement into whichever verdict happened to win would hide exactly
    the instability, which should be surfaced, not smoothed over.
    `ERROR` is the ONE exception: if EVERY draw for a base case reads ERROR,
    the group is `ERROR`, matching rule 3 (ERROR is not FAIL and is never
    PASS) rather than reading a case that never once ran as merely
    "unstable."
    """
    groups: dict[str, list[str]] = {}
    for case_row, outputs in zip(report.cases, report.detailed_results, strict=True):
        base = base_name(case_row["name"])
        if not outputs:
            # An unanswered row means the draw's evaluator produced no
            # verdict — an errored draw, never an absent one.
            groups.setdefault(base, []).append(ERROR)
        for out in outputs:
            label = getattr(out, "label", None)
            groups.setdefault(base, []).append(label if label in (PASS, FAIL, ERROR) else ERROR)

    readings: dict[str, SampleReading] = {}
    for base, labels in groups.items():
        distinct = set(labels)
        verdict = labels[0] if len(distinct) == 1 else UNSTABLE
        readings[base] = SampleReading(
            verdict=verdict,
            passed=labels.count(PASS),
            failed=labels.count(FAIL),
            errored=labels.count(ERROR),
        )
    return readings
