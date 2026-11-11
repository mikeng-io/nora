"""The fail-closed verdict roll-up.

A judge answers each expectation `met`, `not_met` or `cannot_tell`, with
a reason and the state-entry ids it rests on. A reason citing an id the
wake's diff or state does not hold is `ERROR` — a malformed answer, a
judge outage, a model-identity mismatch, a restore/advance/wake failure:
anything the run cannot ground never reads as a pass.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

PASS = "PASS"  # noqa: S105 — a verdict word, not a credential
FAIL = "FAIL"
REVIEW = "REVIEW"
ERROR = "ERROR"

#: A single judge's answer to one expectation.
JUDGE_VERDICTS = ("met", "not_met", "cannot_tell")


@dataclass(frozen=True, slots=True)
class JudgeAnswer:
    verdict: str  # one of JUDGE_VERDICTS, or "error" for a judge failure
    reason: str = ""
    entry_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.verdict not in (*JUDGE_VERDICTS, "error"):
            raise ValueError(f"a judge answers met|not_met|cannot_tell|error, got {self.verdict!r}")


@dataclass(frozen=True, slots=True)
class ExpectationVerdict:
    """Both judges' answers to one expectation, rolled up."""

    verdict: str  # PASS | FAIL | REVIEW | ERROR
    answers: tuple[JudgeAnswer, ...]
    detail: str = ""


def validate_citations(
    answer: JudgeAnswer, known_ids: Iterable[str],
) -> str | None:
    """A cited entry must exist in the wake's diff or state. Returns the
    bogus id, or None."""
    known = set(known_ids)
    for entry_id in answer.entry_ids:
        if entry_id not in known:
            return entry_id
    return None


def expectation_verdict(answers: tuple[JudgeAnswer, ...]) -> ExpectationVerdict:
    """Two judges, fail-closed: an `error` answer or disagreement on a
    decidable answer is REVIEW; `met` by both is the only way through."""
    if not answers:
        return ExpectationVerdict(verdict=ERROR, answers=(), detail="no judge answered")
    if any(a.verdict == "error" for a in answers):
        return ExpectationVerdict(verdict=ERROR, answers=answers,
                                  detail="a judge could not answer")
    verdicts = {a.verdict for a in answers}
    if verdicts == {"met"}:
        return ExpectationVerdict(verdict=PASS, answers=answers)
    if "not_met" in verdicts and len(verdicts) == 1:
        return ExpectationVerdict(verdict=FAIL, answers=answers)
    return ExpectationVerdict(verdict=REVIEW, answers=answers,
                              detail="the judges disagree or cannot tell")


@dataclass(slots=True)
class Rollup:
    """The run's verdicts at every grain: expectation, wake, case."""

    verdict: str = PASS
    errors: list[str] = field(default_factory=list)
    expectations: list[ExpectationVerdict] = field(default_factory=list)

    def error(self, why: str) -> None:
        self.verdict = ERROR
        self.errors.append(why)

    def add(self, verdict: ExpectationVerdict) -> None:
        self.expectations.append(verdict)
        self.verdict = rollup(self.verdict, verdict.verdict)


def rollup(*verdicts: str) -> str:
    """ERROR > FAIL > REVIEW > PASS: an error never passes, a failure never
    reviews away, disagreement is never silent."""
    out = PASS
    for v in verdicts:
        if v == ERROR:
            return ERROR
        if v == FAIL:
            out = FAIL
        elif v == REVIEW and out == PASS:
            out = REVIEW
    return out
