"""The doctrine check: does a lesson loosen or contradict a LAW rule?

A separate instrument call (a model other than the Lessons session) reads one
lesson and the law subset (DEC-291) and answers which rule, if any, the lesson
pulls against. Code applies its verdict: a conflict refuses the lesson and writes a
`Tension` row for the operator; anything the instrument did not answer cleanly is
an `error`, which also refuses the lesson (**an instrument error is not a pass**)
and writes no row.

Pure: the parse of the answer and the types. The call is
`nora.language.doctrine_check.DoctrineCheck`; the table is
`nora.infra.sql.tension.SqlTensionStore`.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

__all__ = [
    "CHECK_OUTCOMES",
    "LAW_SURFACES",
    "CheckVerdict",
    "LawRule",
    "Tension",
    "parse_verdict",
]

#: The doctrine folders whose law reaches a unit's turns, where a lesson renders.
#: `public/` (no unit) and `instruments/` (other models' instructions) carry
#: law too, but no lesson ever reaches those surfaces, so reading them would only
#: let a lesson about a group chat be refused by a rule about the public surface.
LAW_SURFACES = ("core/", "rooms/")

#: `nora_doctrine_check_total{outcome}`.
CHECK_OUTCOMES = frozenset({"consistent", "conflict", "error"})


@dataclass(frozen=True, slots=True)
class LawRule:
    """One law rule as the check reads it: where it is and what it says."""

    file: str
    line: int
    text: str


@dataclass(frozen=True, slots=True)
class CheckVerdict:
    """`outcome` is `consistent`, `conflict` (then `rule` is the rule pulled
    against) or `error` (then `why` says what was wrong with the answer)."""

    outcome: str
    rule: LawRule | None = None
    why: str = ""


@dataclass(frozen=True, slots=True)
class Tension:
    """One refused lesson and the law it pulled against. Operator-only."""

    unit: str
    source: str
    doctrine_file: str
    doctrine_line: int
    doctrine_text: str
    would_have_said: str
    citations: tuple[str, ...]
    why: str
    written_at: int
    established_by: str
    instruction_sha256: str | None = None
    model: str | None = None
    check_model: str | None = None
    id: str = ""


def parse_verdict(
    *, content: str, finish_reason: str, rules: Sequence[LawRule],
) -> CheckVerdict:
    """The instrument's answer, read strictly: a JSON object with `verdict`
    (`consistent` or `conflict`), and for a conflict a `rule` number that is one
    of the rules it was shown and a non-empty `why`. Anything else is `error`,
    never a pass."""
    if finish_reason == "length":
        return CheckVerdict("error", why="the answer was cut off")
    try:
        data = json.loads(content)
    except (TypeError, ValueError):
        return CheckVerdict("error", why="the answer is not JSON")
    if not isinstance(data, dict):
        return CheckVerdict("error", why="the answer is not a JSON object")
    verdict = data.get("verdict")
    if verdict == "consistent":
        return CheckVerdict("consistent")
    if verdict != "conflict":
        return CheckVerdict("error", why=f"unknown verdict {verdict!r}")
    number = data.get("rule")
    if isinstance(number, str) and number.strip().isdigit():
        number = int(number.strip())
    if not isinstance(number, int) or isinstance(number, bool) or not 1 <= number <= len(rules):
        return CheckVerdict("error", why=f"a conflict naming no rule it was shown ({number!r})")
    why = data.get("why")
    if not isinstance(why, str) or not why.strip():
        return CheckVerdict("error", why="a conflict with no reason")
    return CheckVerdict("conflict", rule=rules[number - 1], why=why.strip()[:1000])
