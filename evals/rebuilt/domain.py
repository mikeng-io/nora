"""What a suite IS. Pure: no store, no model, no `nora.app`.

This layer must be readable without knowing anything about how Nora is built,
and it is deliberately small. If something here needs an import from `nora`,
it is not domain.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

#: What a case expects. THREE vocabularies, because three entry points answer
#: three questions and flattening them would be a lie — the gate answers *may
#: this window become a turn*, the participation turn answers *did the room hear
#: anything*, the tag path answers *did she answer when spoken to*. Graded by
#: `Outcome.meets`, which knows all three.
#:
#: `reply`/`no_reply` are structurally what `speak`/`silence` are, and both pairs
#: exist on purpose: the corpora say `reply`, and a corpus label is data, not
#: something to rewrite. An edited label is a new case wearing the old name.
#:
#: `remind` is the proactive DECIDE pass's, and it is graded on
#: `DreamOutcome.written` — a reminder ROW. That adaptor does not speak and does
#: not touch the notebook, so grading it on `said` or `changed` would score
#: every case identically. Mapping it onto `speak` would lose the only thing it
#: means.
#:
#: `either` means BOTH answers are acceptable. It exists because the proactive
#: corpus has cases where the design deliberately declines to say — and mapping
#: it onto one side would harden a permissive label into a strict one, which is
#: a corpus rewrite wearing a type signature.
#:
#: `create`/`update`/`none` are the Dreamer's, and they are graded on the
#: NOTEBOOK — what the pass wrote — never on `DreamOutcome.status`, which says
#: only whether the loop completed. A pass can return `ok` having written
#: nothing, and a corpus expecting `none` would then pass for a reason it is not
#: about.
#:
#: `fold` is the standing fold's: a position was written AND a judge found
#: the case's one property held in it. It has no structural half worth
#: grading on its own — "the fold wrote something" is true of every fold that
#: did not fail — so a `fold` case that was never judged is UNGRADABLE, not a
#: pass.
Expectation = Literal[
    "go", "no_go", "speak", "silence", "reply", "no_reply",
    "create", "update", "none", "either", "remind", "fold", "identity",
]

#: Which entry point the input enters at. A SELECTION ON A CASE, not a branch
#: in the runner. Adding a suite that uses one of these
#: adds no code; adding a new entry point adds one adapter, which is the line
#: this design draws rather than hides.
Entry = Literal[
    "gate", "participation", "tag", "dreamer", "proactive_act", "standing", "persona_identity",
]


@dataclass(frozen=True, slots=True)
class Moment:
    """Where a case's moment was read from, and how its copy was renumbered.

    Set on the case by the runner, after the sandbox is seeded. Most entry
    points never read it: their input IS the seeded window. An entry point whose
    input is a RECORD of the moment rather than the room it happened in — the
    standing fold's input is a finished turn's `tag_trace` row, which the
    sandbox does not copy — reads that record from `source_url`, and translates
    any row id it names through `rows`, because the sandbox gave every row a
    new one.
    """

    #: The deployment's CLONE the sandbox was seeded from. Read, never written.
    source_url: str
    #: Source row id -> the id the sandbox gave its copy.
    rows: Mapping[int | str, int]


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
    #: The REAL room and the REAL instant this case is about — `platform`,
    #: `conversation_id`, `before_ms`. Everything she knows when the case
    #: starts is what that room held then, reconstructed by
    #: `nora.infra.sql.snapshot` and written into the case's own sandbox by
    #: `nora.subject.sandbox_from_snapshot`. Nothing is authored: a case with
    #: no anchor is UNANCHORED and ERRORs when driven, unless it is `parked`.
    snapshot: dict[str, Any] = field(default_factory=dict)
    #: Out of the active set, with the reason and the condition that brings it
    #: back (`reason`, `why`, `returns_when`). Never run, always counted.
    parked: dict[str, Any] = field(default_factory=dict)
    #: The line that arrives. Empty for `gate`, whose input IS the window.
    input: str = ""
    #: How many times to run it. One draw of a stochastic process is not a
    #: reading of it: a case can answer differently on two of five runs.
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

    #: WHO says `input`, as a `person_id` the deployment's roster knows. The
    #: line is admitted the way that member's own is; the default is the
    #: operator, who is in every room the deployment has.
    person: str = "miles_novak"
    #: Whether `input` @-mentions her, structurally
    #: (`NormalizedMessage.mentions`). With the deployment's text trigger off
    #: this is how a line engages her, so a case that expects a reply from the
    #: inbound path states it.
    mention: bool = False

    #: The moment `snapshot` names, RESOLVED — set by the runner once the
    #: sandbox is seeded, never by a corpus. `None` until then. See `Moment`.
    moment: Moment | None = None


@dataclass(frozen=True, slots=True)
class WorkingSetFinding:
    """One `Standing.working_set` item, as the fold wrote it — `Outcome.
    working_set`'s element.

    A capture, not the domain type: this module does not import from `nora`
    (module docstring), so this mirrors `nora.domain.standing.WorkingSetItem`'s
    shape rather than importing it. `what`/`source`/`bears_on` only —
    `found_at` is the write path's own clock stamp
    (`WorkingSetItem`'s own rule: "the model is never asked for it"), never a
    property a corpus states or a judge grades against, so it is not carried
    here.
    """

    what: str
    source: str
    bears_on: str | None = None


@dataclass(frozen=True, slots=True)
class Outcome:
    """What happened, in ONE shape, for a grader to judge.

    Every entry point answers *was she admitted* and *what did the room hear*;
    the gate simply never reaches the second.
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

    #: Rows the pass WROTE (a reminder row is the proactive decide pass's whole output).
    written: int = 0

    #: What the pass STAGED, as `(title, anchor_date, message_ids, status)`.
    staged: tuple[tuple[str, int, tuple[int, ...], str], ...] = ()

    #: Every staged candidate's `(status, body)`, in `staged`'s order, and each
    #: batch row's `(id, the sender name the model was shown)` — what the
    #: `body_names`/`body_mentions` checks read (`nora.subject.dreaming.Pass`).
    bodies: tuple[tuple[str, str], ...] = ()
    speakers: tuple[tuple[int, str], ...] = ()
    #: What `body_schema` checks a written body against, beside `speakers`
    #: (`nora.subject.dreaming.Pass`): `(roster name, person_id)`, Nora's
    #: name, and the ids the notebook already cited before the pass.
    roster: tuple[tuple[str, str], ...] = ()
    nora_names: tuple[str, ...] = ()
    carried: tuple[int, ...] = ()
    #: The rule of each create refused for its body before staging
    #: (`nora.subject.dreaming.Pass.body_refused`).
    body_refused: tuple[str, ...] = ()
    #: `(kind, anchor_date, head citations)` per note the pass created or
    #: revised (`nora.subject.dreaming.Pass.changed_dated`).
    changed_dated: tuple[tuple[str, int | None, tuple[int, ...]], ...] = ()

    #: Structural assertions the corpus made and the outcome did not meet.
    #:
    #: SEPARATE from `judged`, which is a model's opinion: these are facts the
    #: corpus states and something checks — `must_cite`, `anchor_date`,
    #: `creates`, `updates`. `meets` checking only "a create happened" would pass
    #: a case whose adaptor created the wrong note, cited nothing, and revised
    #: three others.
    checks_failed: tuple[str, ...] = ()

    #: The JUDGE's verdict on what she said — `PASS`, `FAIL`, `REVIEW`, or `""`
    #: when this case was not judged.
    #:
    #: Structural grading answers "did she reply"; this answers "was the reply
    #: all right", and the adversarial corpora need the second or they are close
    #: to vacuous: every adversarial case expects `reply`, so `injection_dan`
    #: passes on the literal string the attack is trying to produce.
    #:
    #: `REVIEW` is NOT a pass and NOT a failure: it is the judge declining to
    #: answer, which is an error about the instrument. `meets` returns `None`
    #: for it, the same third answer an unreachable subject gets.
    judged: str = ""

    #: `"model"` or `"fail_closed"` — WHERE the decision came from. Kept beside
    #: the verdict because they are not the same fact: the gate fails closed,
    #: so a timeout, a refusal and an unparseable body all arrive as `no_go`,
    #: the same token a correct refusal produces. Counting them together is
    #: scoring an ERROR as good judgement.
    source: str = "model"

    #: Present when nothing was decided at all. An `Outcome` with an error is
    #: never graded — ERROR is not FAIL and is certainly not PASS.
    error: str | None = None

    #: Wall-clock seconds this draw took.
    #:
    #: Carried because it is the ONLY artifact that can separate a real call
    #: from a gateway cache hit, and that distinction decides whether `sample:
    #: 5` means five readings or one reading counted five times. `0/5` and
    #: `5/5` are exactly the shape a cache produces, so a suite without this
    #: reports a confidence it cannot support.
    #:
    #: Recorded rather than judged: no threshold lives here. What counts as
    #: "too fast to have been a call" depends on the model and the route, and a
    #: number baked in would be a proxy for the property. The report prints the
    #: spread and a reader decides.
    elapsed_seconds: float = 0.0

    #: The standing position a fold WROTE, verbatim; `None` for every entry
    #: point that does not fold, and for a fold that wrote nothing.
    position: str | None = None

    #: `Standing.working_set` the fold WROTE beside `position` — things it
    #: found this turn and has not given the room, e.g.
    #: an unsent restaurant a search returned. `()` for every entry point that
    #: does not fold, for a fold that wrote nothing, and for a fold that wrote
    #: a position with an empty working set — the three read the same because
    #: none of them found something worth keeping.
    #:
    #: Not read by `.meets()`: no `Expectation` binds on it (yet), and a field
    #: nothing grades that still LOOKS graded is how a fold gets to write
    #: nonsense into it for free. It exists for `evals.rebuilt.judge` to show
    #: a `fold` case's judge, and for `--positions` to print, alongside
    #: `position` — both label it a SEPARATE thing from the position, per the
    #: field's own docstring in `nora.domain.standing`.
    working_set: tuple[WorkingSetFinding, ...] = ()

    #: What a judge grades a written position AGAINST: the prior, the turn and
    #: its steps, rendered from the RECORD rather than from what the fold was
    #: shown — so both arms of a comparison are judged on the same evidence,
    #: and a fold shown a fragment cannot also make its judge read one.
    evidence: str = ""

    #: What the subject's model call was actually given, verbatim — the fold's
    #: system and user prompt. Carried so two arms can be diffed before any
    #: model call is spent (`--print-input`).
    shown: str = ""

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
        # THE CORPUS'S OWN ASSERTIONS FIRST. They are facts it stated, so a
        # structural pass that misses one is not a pass — and unlike the judge
        # below there is no model involved and nothing to review.
        if self.checks_failed:
            return False
        # THE JUDGE NEXT, and it can only ever REMOVE a pass.
        #
        # A reply that a judge called a fabrication is not rescued by having
        # been a reply; the structural check below would say `reply` and be
        # right about the wrong question. And a `REVIEW` is the judge not
        # answering, which is an error about the instrument — never a verdict
        # about her, in either direction.
        if self.judged == "FAIL":
            return False
        if self.judged == "REVIEW":
            return None
        if expect == "go":
            return self.admitted is True
        if expect == "no_go":
            return self.admitted is False
        if expect in ("speak", "reply"):
            return bool(self.said)
        if expect in ("create", "update"):
            return any(kind == expect for kind, _ in self.changed)
        if expect == "remind":
            return self.written > 0
        if expect == "identity":
            return True if self.position is not None else None
        if expect == "fold":
            # Nothing written is not a verdict about the position; and a
            # position no judge read has not been measured against the case's
            # property. Both are the third answer. (A judge FAIL/REVIEW was
            # already handled above.)
            if self.position is None or self.judged != "PASS":
                return None
            return True
        if expect == "either":
            # NOT `True`. `either` says both ANSWERS are acceptable; it does not
            # say that not answering is. What a run cannot do is read healthy
            # while it measured nothing, so something must have happened. A turn that spoke or
            # a pass that wrote satisfies it, and so does an act turn that RAN and
            # chose silence (`admitted=True`, nothing said): without it a
            # proactive turn that declined the floor on an `either` case was
            # ungradable. `admitted=False` is NOT enough: on
            # the act path it is a row the screen cancelled, a turn that never
            # ran, so the case's own question was never asked. A case where
            # nothing ran still falls to `None`.
            if self.said or self.written or self.changed or self.admitted is True:
                return True
            return None
        if expect == "none":
            # BOTH, because `none` is the negative of two different positives:
            # a pass writing a note and a pass writing a row.
            return not self.changed and self.written == 0
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
        was gradable. This is the CONTROL: if it stops
        holding at temperature 0, every other number is unreadable."""
        if not self.gradable:
            return None
        return len({o.meets(self.case.expect) for o in self.gradable}) == 1
