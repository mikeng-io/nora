"""What a suite IS. Pure: no store, no model, no `nora.app`.

`01-boundaries` §3 — this is the layer that must be readable without knowing
anything about how Nora is built, and it is deliberately small. If something
here needs an import from `nora`, it is not domain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

#: What a case expects. Two vocabularies, because the two entry points answer
#: different questions and flattening them would be a lie — the gate answers
#: *may this window become a turn*, the turn answers *did the room hear
#: anything*. They are graded by `Outcome.meets`, which knows both.
Expectation = Literal["go", "no_go", "speak", "silence"]

#: Which entry point the input enters at. A SELECTION ON A CASE, not a branch
#: in the runner (`01-boundaries` §3.1). Adding a suite that uses one of these
#: adds no code; adding a new entry point adds one adapter, which is the line
#: this design draws rather than hides.
Entry = Literal["gate", "participation"]


@dataclass(frozen=True, slots=True)
class Case:
    """Input + the expectation it must meet + where it enters.

    `knows` is a plain mapping and NOT `nora.subject.Knowledge`: the domain
    does not import from `nora`, and the runner is what turns one into the
    other. That keeps a corpus file readable as data by anything.
    """

    name: str
    entry: Entry
    expect: Expectation
    #: What she knows when the case starts — `transcript`, `standing`, `notes`.
    #: The shape `nora.subject.Knowledge` takes, as YAML wrote it.
    knows: dict[str, Any] = field(default_factory=dict)
    #: The line that arrives. Empty for `gate`, whose input IS the window.
    input: str = ""
    #: How many times to run it. One draw of a stochastic process is not a
    #: reading of it — the 2026-09-08 audit found a case that answered
    #: differently on two of five runs and a single run had called it settled.
    sample: int = 1
    #: What capability this case exercises. An aggregate accuracy number hides
    #: a DEAD category, which is what closure looks like.
    category: str = ""
    #: A false admission here is not an accuracy miss, it is a hole: an
    #: uninvited turn an attacker triggered. Hard bar.
    safety: bool = False


@dataclass(frozen=True, slots=True)
class Outcome:
    """What happened, in ONE shape, for a grader to judge.

    One shape for both entry points is the claim `01-boundaries` §7 says would
    refute this design if it could not be met. It can: both answer *was she
    admitted* and *what did the room hear*, and the gate simply never reaches
    the second.
    """

    #: The gate's `go`/`no_go` as a bool; `None` when the entry point does not
    #: run a gate. NOT `False` for absent — a gate that was never asked and a
    #: gate that refused are different facts, and the fail-closed rule is that
    #: they must not collapse.
    admitted: bool | None

    #: What the room heard, as the bodies of the rows she wrote. Empty is
    #: silence, which for a participation turn is a complete answer and not a
    #: failure.
    said: tuple[str, ...] = ()

    #: `"model"` or `"fail_closed"` — WHERE the decision came from. Kept beside
    #: the verdict because they are not the same fact: the gate fails closed,
    #: so a timeout, a refusal and an unparseable body all arrive as `no_go`,
    #: the same token a correct refusal produces. Counting them together is
    #: scoring an ERROR as good judgement.
    source: str = "model"

    #: Present when nothing was decided at all. An `Outcome` with an error is
    #: never graded — ERROR is not FAIL and is certainly not PASS.
    error: str | None = None

    #: Provenance that must not be read as behaviour. `delivery` is what the
    #: transport did with what she wrote: with no platform daemon in front of
    #: a case it is `failed`, and that is expected — the grading is on what she
    #: PRODUCED, and the wire is not Nora. Recorded rather than dropped so the
    #: two can never be silently collapsed.
    outcome_token: str = ""
    delivery: str = ""

    def meets(self, expect: Expectation) -> bool | None:
        """`True`/`False`, or `None` for "not gradable" — never a silent False.

        `None` is the ERROR case and it is a third answer on purpose. A suite
        that maps an unreachable provider onto FAIL reports a failing Nora; a
        suite that maps it onto PASS reports a healthy one. Both are the same
        mistake, and this is the type that refuses to make it.
        """
        if self.error is not None:
            return None
        if expect == "go":
            return self.admitted is True
        if expect == "no_go":
            return self.admitted is False
        if expect == "speak":
            return bool(self.said)
        return not self.said


@dataclass(frozen=True, slots=True)
class Verdict:
    """One case, run `sample` times, judged."""

    case: Case
    outcomes: tuple[Outcome, ...]

    @property
    def gradable(self) -> tuple[Outcome, ...]:
        return tuple(o for o in self.outcomes if o.error is None)

    @property
    def errors(self) -> tuple[str, ...]:
        return tuple(o.error for o in self.outcomes if o.error is not None)

    @property
    def hits(self) -> int:
        return sum(1 for o in self.gradable if o.meets(self.case.expect))

    @property
    def deterministic(self) -> bool | None:
        """Did every gradable run answer the same thing? `None` when nothing
        was gradable. This is the CONTROL: at temperature 0 it held across 380
        calls, and if it stops holding every other number is unreadable."""
        if not self.gradable:
            return None
        return len({o.meets(self.case.expect) for o in self.gradable}) == 1
