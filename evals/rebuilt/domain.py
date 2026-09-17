"""What a suite IS. Pure: no store, no model, no `nora.app`.

`01-boundaries` §3 — this is the layer that must be readable without knowing
anything about how Nora is built, and it is deliberately small. If something
here needs an import from `nora`, it is not domain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

#: What a case expects. THREE vocabularies, because three entry points answer
#: three questions and flattening them would be a lie — the gate answers *may
#: this window become a turn*, the participation turn answers *did the room hear
#: anything*, the tag path answers *did she answer when spoken to*. Graded by
#: `Outcome.meets`, which knows all three.
#:
#: `reply`/`no_reply` are structurally what `speak`/`silence` are, and both pairs
#: survive on purpose: the corpora say `reply`, and Step 3's rule is data, not
#: rewrites. A label edited during a move is a new case wearing the old name.
#:
#: `create`/`update`/`none` are the Dreamer's, and they are graded on the
#: NOTEBOOK — what the pass wrote — never on `DreamOutcome.status`, which says
#: only whether the loop completed. A pass can return `ok` having written
#: nothing, and a corpus expecting `none` would then pass for a reason it is not
#: about.
Expectation = Literal[
    "go", "no_go", "speak", "silence", "reply", "no_reply",
    "create", "update", "none",
]

#: Which entry point the input enters at. A SELECTION ON A CASE, not a branch
#: in the runner (`01-boundaries` §3.1). Adding a suite that uses one of these
#: adds no code; adding a new entry point adds one adapter, which is the line
#: this design draws rather than hides.
Entry = Literal["gate", "participation", "tag", "dreamer"]


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

    #: What a good answer looks like, in prose, for a JUDGE to grade against.
    #: Structural grading (`Outcome.meets`) answers "did she reply"; this is the
    #: half that answers "was the reply right", and it is deliberately not
    #: machine-readable — a rubric that a string comparison could check is a
    #: rubric that is measuring spelling.
    expected_output: str = ""

    #: Whatever else the corpus recorded about this case, carried verbatim.
    #:
    #: `trigger.yaml` uses it for `prefill_sufficient` — whether the case should
    #: be answerable from the prefill alone, with no tool call. Nothing grades
    #: that yet. It is a field rather than a dropped key because a corpus is
    #: DATA: silently ignoring a key is how a fact recorded once stops existing,
    #: and the next person to want it has to re-derive it by hand.
    metadata: dict[str, Any] = field(default_factory=dict)

    #: `policy.toml`, when this suite needs a room set the default does not
    #: give. Corpus-level in practice, like `config:`.
    #:
    #: Separate from `config:` because it is NOT configuration: the proactive
    #: adaptor has no config flag at all — `nora/app/composition.py:3208` says
    #: so — and is wired only when `policy.toml` names a HOME ROOM, because the
    #: home room IS the operator's opt-in. A suite about the proactive adaptor
    #: therefore states a policy, and no amount of dotted config paths can say
    #: it.
    policy: str = ""

    #: Config this case's SUITE needs, as dotted paths. Applied under the entry
    #: point's own requirements and over nothing else.
    #:
    #: Corpus-level and not per-case in practice: `trigger.yaml` and
    #: `no_trigger.yaml` are built on the literal trigger `"nora.000"` and every
    #: near-miss in them is a near-miss *of that string*, so the trigger text is
    #: a property of the corpus. `load()` copies the file's `config:` onto every
    #: case in it, which keeps the file readable as data while leaving each case
    #: self-contained once loaded.
    config: dict[str, Any] = field(default_factory=dict)


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

    #: What the pass changed, as `("create", title)` / `("update", title)`.
    #: Empty for every entry point that writes no notes.
    changed: tuple[tuple[str, str], ...] = ()

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
        if expect in ("speak", "reply"):
            return bool(self.said)
        if expect in ("create", "update"):
            return any(kind == expect for kind, _ in self.changed)
        if expect == "none":
            return not self.changed
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
