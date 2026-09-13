"""The proactive adaptor's two suites — cases, structural grading, summary
(design/dreamer-agent-loop/39 §5, DEC-162 (10)).

**Two suites, because the loop has two turns.** The DECIDE suite measures
WHEN — the thing that makes the adaptor proactive rather than scheduled: a
workspace's open notes, per-room Standing, the decision log and memory go
in; the reminder rows the real runtime + real adaptor left behind come out,
and PASS is exactly the expected rows, each inside its `due_between`
window, owner `nora`, kind `proactive`. The ACT suite measures the fire
turn: one proactive row plus the room at fire time, graded on
`Trajectory.sent`'s shape — `speak` with `must_cite`, or `silent`. Both
grade STRUCTURALLY on the state left behind, no judge.

**Fail-closed.** `unavailable` (decide) and `failed_*` (act) are ERROR for
every case — a timeout that looks like a correct `none` is R-27's forbidden
substitution — and the summary reports errors apart from failures.

**The corpus is majority `none`/`silent`, and the false-positive rate on
those is the PRIMARY number** (39 §5): an ambient agent's costly failure is
being annoying, not being late. The labels are one rater's threshold — the
operator's — so the rate is relative to that threshold, not absolute.

This module holds the parts that need no model; `evals.run`'s two
`_run_proactive_*_live` functions drive it.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml

DECIDE_EXPECTS: tuple[str, ...] = ("none", "remind")
#: `either` is the third act expectation and it grades the MESSAGE rather than
#: the decision (issue #226): a case where speaking and staying quiet are both
#: defensible, and what is under test is what the room hears IF it hears
#: anything. It exists because grading the decision on such a case measures the
#: threshold instead — measured on the two #226 cases, 2/5 and 3/5 spoken over
#: identical prompts, so a `speak` label there would have reported her mood and
#: nothing about the defect. A silent sample is UNMEASURED, never PASS: the case
#: measured nothing, and an unmeasured sample that reads as a pass is exactly
#: R-27's forbidden substitution.
ACT_EXPECTS: tuple[str, ...] = ("silent", "speak", "either")

#: Four states, not three, and the fourth was bought with a red gate.
#:
#: `either` + silence was first graded ERROR, on the argument that an outage and
#: a non-measurement are the same shape. They are not, and R-27 is the rule that
#: says so: ERROR means «I could not check» — a timeout, a refused send, a
#: budget cut — and something is WRONG when one appears. UNMEASURED means «there
#: was nothing to check», and on an `either` case that is the model taking an
#: option the case's own YAML declares defensible. Collapsing them cost both
#: halves at once: the gate went red on a case behaving as declared (because
#: `majority_pass` has no graded sample to hold a majority over), and the gate
#: LINE reported `errors 9` on a run with zero outages, inverting the one thing
#: its docstring promises — that an outage cannot read as a quiet model.
#:
#: The two never mix again: `CaseTally.errors` counts outages only, the rates
#: skip both, and `act_case_ok` is the single place that says what a fully
#: unmeasured case does to the verdict.
Verdict = Literal["PASS", "FAIL", "ERROR", "UNMEASURED"]


def _iso(value: str) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError(f"timestamp {value!r} needs an offset")
    return dt.astimezone(UTC)


# ---------------------------------------------------------------------------
# decide
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExpectedRow:
    note: str
    due_between: tuple[datetime, datetime]


@dataclass(frozen=True)
class DecideCase:
    name: str
    expect: str
    workspace: str
    now: datetime
    rooms: tuple[dict[str, Any], ...]
    notes: tuple[dict[str, Any], ...] = ()
    standing: tuple[dict[str, Any], ...] = ()
    log: tuple[dict[str, Any], ...] = ()
    memory: dict[str, Any] = field(default_factory=dict)
    rows: tuple[ExpectedRow, ...] = ()
    #: A CEILING on memory reads, for a case whose point is the COST of
    #: reaching the decision rather than the decision (issue #145). The suite
    #: otherwise grades only what was written, and the failure this exists for
    #: is a pass that spends six searches resolving a name the index should
    #: have carried — a correct decision, reached the expensive way, which no
    #: other assertion here can see. Absent means unbounded, which is every
    #: case that predates it.
    max_memory_calls: int | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> DecideCase:
        case = cls(
            name=str(raw["name"]), expect=str(raw["expect"]),
            workspace=str(raw["workspace"]), now=_iso(raw["now"]),
            rooms=tuple(dict(r) for r in raw["rooms"]),
            notes=tuple(dict(n) for n in raw.get("notes", ())),
            standing=tuple(dict(s) for s in raw.get("standing", ())),
            log=tuple(dict(entry) for entry in raw.get("log", ())),
            memory=dict(raw.get("memory") or {}),
            rows=tuple(
                ExpectedRow(
                    note=str(r["note"]),
                    due_between=(_iso(r["due_between"][0]), _iso(r["due_between"][1])),
                )
                for r in raw.get("rows", ())
            ),
            max_memory_calls=(
                int(raw["max_memory_calls"])
                if raw.get("max_memory_calls") is not None else None
            ),
        )
        case.validate()
        return case

    def validate(self) -> None:
        """Refuse a case that cannot be graded, at LOAD time."""
        if self.expect not in DECIDE_EXPECTS:
            raise ValueError(f"{self.name}: expect must be one of {DECIDE_EXPECTS}")
        if sum(1 for r in self.rooms if r.get("home")) != 1:
            raise ValueError(f"{self.name}: exactly one room must be the home room")
        labels = [str(r["label"]) for r in self.rooms]
        if len(set(labels)) != len(labels):
            raise ValueError(f"{self.name}: room labels must be distinct")
        titles = [str(n["title"]) for n in self.notes]
        if len(set(titles)) != len(titles):
            raise ValueError(f"{self.name}: note titles must be distinct")
        for s in self.standing:
            if str(s["room"]) not in labels:
                raise ValueError(f"{self.name}: standing names an unknown room {s['room']!r}")
        if self.expect == "none" and self.rows:
            raise ValueError(f"{self.name}: a `none` case has no rows")
        if self.expect == "remind" and not self.rows:
            raise ValueError(f"{self.name}: a `remind` case needs at least one row")
        for row in self.rows:
            if row.note not in titles:
                raise ValueError(f"{self.name}: row names a note not in the case: {row.note!r}")
            lo, hi = row.due_between
            if not lo < hi:
                raise ValueError(f"{self.name}: due_between must be ordered")
            if hi <= self.now:
                raise ValueError(f"{self.name}: a due_between window entirely before `now`")


def load_decide_cases(path: Path | str) -> list[DecideCase]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    cases = [DecideCase.from_dict(item) for item in raw]
    _distinct(cases)
    return cases


@dataclass(frozen=True)
class WrittenRow:
    note: str | None
    due_at: datetime
    requester: str
    kind: str
    intent: str


@dataclass(frozen=True)
class DecideResult:
    status: str
    stop_reason: str | None
    instruction_sha256: str
    rows: tuple[WrittenRow, ...]
    #: `remind` calls the model made, created or refused. The store shows
    #: only the created ones; a quiet case must not pass because the closure
    #: refused what the model tried to write.
    remind_attempts: int = 0
    #: Memory reads the turn made (`about`, `ask_memory`, `lookup_person`,
    #: `search_memory_workspace`), any status. Objective version 2 requires
    #: one before any reminder; a `remind` case without one FAILS however
    #: well-timed the row, because the timing was not hers to know.
    #:
    #: `lookup_person` counts since 2026-09-11 and the omission was a live
    #: false FAIL, not a gap: `d11` is the case that can only be decided by
    #: resolving a handle, so a pass that spent its whole ration on the verb
    #: granted for that (issue #145) reported zero reads and failed for doing
    #: the right thing. A read the PASS CEILING refused (R-91, issue #160)
    #: never reaches a tool and so is not here — see `memory_calls_from`.
    memory_calls: int = 0


@dataclass(frozen=True)
class Grade:
    verdict: Verdict
    reason: str = ""

    @property
    def passed(self) -> bool:
        return self.verdict == "PASS"


def grade_decide(case: DecideCase, result: DecideResult) -> Grade:
    """Exactly the expected rows, in their windows, owner nora, kind
    proactive. An extra row is a FAIL whatever the expectation — an
    unasked-for row is the annoying failure this suite exists to count."""
    if result.status == "unavailable":
        return Grade("ERROR", f"runtime unavailable (stop_reason={result.stop_reason})")
    wrong = [r for r in result.rows if r.requester != "nora" or r.kind != "proactive"]
    if wrong:
        return Grade("FAIL", f"a row was written with the wrong owner or kind: {wrong[0]}")
    if case.expect == "none":
        if result.rows:
            return Grade("FAIL", f"a row was written: {[r.note for r in result.rows]}")
        if result.remind_attempts:
            return Grade(
                "FAIL",
                f"remind was attempted {result.remind_attempts}x and refused — "
                "a quiet day she tried to break",
            )
        return Grade("PASS")
    if result.rows and not result.memory_calls:
        return Grade(
            "FAIL",
            "a reminder was set without a single memory read — the objective "
            "requires about/ask_memory/search_memory_workspace before remind",
        )
    if (
        case.max_memory_calls is not None
        and result.memory_calls > case.max_memory_calls
    ):
        return Grade(
            "FAIL",
            f"{result.memory_calls} memory reads for a decision budgeted at "
            f"{case.max_memory_calls} — the answer was already in the prompt",
        )
    remaining = list(result.rows)
    for expected in case.rows:
        match = next((r for r in remaining if r.note == expected.note), None)
        if match is None:
            return Grade("FAIL", f"no row about {expected.note!r}")
        lo, hi = expected.due_between
        if not lo <= match.due_at <= hi:
            return Grade(
                "FAIL",
                f"row about {expected.note!r} due {match.due_at.isoformat()} is outside "
                f"[{lo.isoformat()}, {hi.isoformat()}]",
            )
        remaining.remove(match)
    if remaining:
        return Grade("FAIL", f"extra row(s): {[r.note for r in remaining]}")
    return Grade("PASS")


# ---------------------------------------------------------------------------
# act
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActCase:
    name: str
    expect: str
    now: datetime
    row: dict[str, Any]
    notes: tuple[dict[str, Any], ...]
    transcript: tuple[Any, ...] = ()
    standing: str | None = None
    must_cite: tuple[str, ...] = ()
    #: The NEGATIVE half of the same measurement (issue #226), and the suite had
    #: none: `must_cite` can only say a reply reached the subject, never that it
    #: reached it the wrong way. The two failures this exists for are both
    #: "she spoke, and speaking was right" — a reply carrying her own
    #: bookkeeping's vocabulary («open note», «my note says»), and a reply
    #: repeating a note's summary where the transcript in the same prompt says
    #: otherwise. Graded on whatever the room HEARD, so a `silent` case
    #: satisfies it vacuously and the substrings must be chosen to appear only
    #: when the reply is actually wrong. `_banned_hits` is what holds a term to
    #: that bar — bare `note` as a plain substring did not, and banned «noted»,
    #: «notebook» and «denote» with it.
    must_not_cite: tuple[str, ...] = ()
    note_moved: bool = False

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ActCase:
        case = cls(
            name=str(raw["name"]), expect=str(raw["expect"]), now=_iso(raw["now"]),
            row=dict(raw["row"]),
            notes=tuple(dict(n) for n in raw.get("notes", ())),
            transcript=tuple(raw.get("transcript", ())),
            standing=raw.get("standing"),
            must_cite=tuple(str(c) for c in raw.get("must_cite", ())),
            must_not_cite=tuple(str(c) for c in raw.get("must_not_cite", ())),
            note_moved=bool(raw.get("note_moved", False)),
        )
        case.validate()
        return case

    def validate(self) -> None:
        if self.expect not in ACT_EXPECTS:
            raise ValueError(f"{self.name}: expect must be one of {ACT_EXPECTS}")
        if "note" not in self.row or "intent" not in self.row:
            raise ValueError(f"{self.name}: row needs `note` and `intent`")
        titles = [str(n["title"]) for n in self.notes]
        if str(self.row["note"]) not in titles:
            raise ValueError(f"{self.name}: row names a note the case does not seed")
        if self.expect == "silent" and self.must_cite:
            raise ValueError(f"{self.name}: a `silent` case has no must_cite")
        if self.expect == "either" and not (self.must_cite or self.must_not_cite):
            raise ValueError(
                f"{self.name}: an `either` case grades only what was heard, so it "
                "needs must_cite or must_not_cite — without either it asserts nothing"
            )
        overlap = set(self.must_cite) & set(self.must_not_cite)
        if overlap:
            raise ValueError(
                f"{self.name}: {sorted(overlap)} is both required and forbidden"
            )
        if self.expect == "speak" and self.note_moved:
            raise ValueError(f"{self.name}: a `speak` case cannot have note_moved (screened)")


def load_act_cases(path: Path | str) -> list[ActCase]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    cases = [ActCase.from_dict(item) for item in raw]
    _distinct(cases)
    return cases


@dataclass(frozen=True)
class ActResult:
    outcome: str
    sent: tuple[str, ...]
    stop_reason: str | None
    screened: str | None = None
    #: `nora_silent_failures_total{audience="proactive"}` — a guard-refused
    #: send, a budget cut, an invocation error. Not a decision.
    silent_failures: int = 0


def _banned_hits(heard: str, banned: Sequence[str]) -> list[str]:
    """Which `must_not_cite` terms a reply actually carries.

    Case-INSENSITIVE, unlike `must_cite`: a ban list is the negative half of
    the measurement and «Note» must not pass where «note» fails. `must_cite`
    stays exact — it names a word the room used, and case is part of it.

    A term whose FIRST or LAST character is an ASCII word character is anchored
    at that end against an adjacent ASCII word character. Bare `note` as a
    plain substring failed the bar `ActCase.must_not_cite` sets for itself —
    «appear only when the reply is actually wrong» — because it also fires on
    «noted», «notebook» and «denote», all of which an entirely correct reply
    may use. The anchor is applied per END, so a multi-word term like «my note»
    is still held on both of its outer edges.

    **Anchored on an ASCII class, NOT on `\\b`, and that is the whole point.**
    `\\b` is defined over `\\w`, which every CJK character satisfies, so
    `\\bnote\\b` would refuse to match 「我個note仲未close」 — an ASCII word
    pressed against Chinese with no space, which is exactly how this room
    writes. The anchor has to ask «is the neighbour an English letter or
    digit», and 個 is not. A reply that names the apparatus in running
    Cantonese is caught; «noted» and «notebook» are not.

    A term with a CJK edge gets no anchor at that end — there is nothing to
    anchor on, Chinese having no inter-word space — so it is a plain substring
    and has to be chosen tightly enough to survive that, which is why the ban
    list carries 「提醒自己」 and not 「提醒」.
    """
    def ascii_word(ch: str) -> bool:
        return ch.isascii() and (ch.isalnum() or ch == "_")

    hits: list[str] = []
    for term in banned:
        if not term:
            continue
        left = r"(?<![A-Za-z0-9_])" if ascii_word(term[0]) else ""
        right = r"(?![A-Za-z0-9_])" if ascii_word(term[-1]) else ""
        if re.search(left + re.escape(term) + right, heard, re.IGNORECASE):
            hits.append(term)
    return hits


def grade_act(case: ActCase, result: ActResult) -> Grade:
    """`speak`: the room heard something, every `must_cite` substring is in
    it and no `must_not_cite` one is. `silent`: the room heard nothing — a
    row the screen cancelled before any model call counts as silent, which
    is the point of the screen. `failed_*` is ERROR: a turn that timed out
    is not a decision. `either` + silence is UNMEASURED, which is the
    fourth state and NOT an error — see `Verdict`.

    `must_not_cite` is checked on ANYTHING that was heard, before the
    positive half and on either expectation, because the reply it catches is
    one that would otherwise pass: the subject is right and the words came
    out of her own bookkeeping rather than out of the room (issue #226).
    """
    if result.outcome.startswith("failed_"):
        return Grade("ERROR", f"fire path {result.outcome} (stop_reason={result.stop_reason})")
    if result.silent_failures:
        return Grade(
            "ERROR",
            f"{result.silent_failures} silent failure(s) under the proactive audience — "
            "a refused send or a budget cut is not a decision",
        )
    heard = bool(result.sent)
    joined = "\n".join(result.sent)
    banned = _banned_hits(joined, case.must_not_cite)
    if heard and banned:
        return Grade("FAIL", f"reply said {banned}: {joined[:120]!r}")
    if case.expect == "silent":
        if heard:
            return Grade("FAIL", f"the room heard: {result.sent[0][:80]!r}")
        return Grade("PASS")
    if case.expect == "either":
        # Silence is not wrong here and it is not a pass either — there was no
        # message, so the thing this case measures did not happen. UNMEASURED
        # keeps it out of both rates and out of `majority_pass`'s denominator
        # WITHOUT claiming an outage: this case's YAML says staying quiet is
        # defensible on this input, so the run is intact and the sample is
        # simply empty. `act_case_ok` decides what that does to the verdict.
        if not heard:
            return Grade("UNMEASURED", "the room heard nothing — defensible here, "
                                       "so this sample measured nothing")
        missing = [c for c in case.must_cite if c not in joined]
        return Grade("FAIL", f"reply did not cite {missing}") if missing else Grade("PASS")
    if not heard:
        why = f"screened: {result.screened}" if result.screened else f"outcome {result.outcome}"
        return Grade("FAIL", f"the room heard nothing ({why})")
    missing = [c for c in case.must_cite if c not in joined]
    if missing:
        return Grade("FAIL", f"reply did not cite {missing}")
    return Grade("PASS")


# ---------------------------------------------------------------------------
# summary over samples — the false-positive rate is the primary number
# ---------------------------------------------------------------------------


@dataclass
class CaseTally:
    name: str
    expect: str
    verdicts: list[Verdict] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def passes(self) -> int:
        return sum(1 for v in self.verdicts if v == "PASS")

    @property
    def errors(self) -> int:
        """OUTAGES only. UNMEASURED is deliberately not counted here: a run
        where every `either` case stayed quiet is a healthy run that measured
        less, and folding it in here inverted `_print_proactive_gate`'s own
        promise — «an outage cannot read as a quiet model» — into a quiet model
        reading as nine outages."""
        return sum(1 for v in self.verdicts if v == "ERROR")

    @property
    def unmeasured(self) -> int:
        return sum(1 for v in self.verdicts if v == "UNMEASURED")

    @property
    def majority_pass(self) -> bool:
        graded = [v for v in self.verdicts if v not in ("ERROR", "UNMEASURED")]
        return bool(graded) and self.passes * 2 > len(graded)

    @property
    def fully_unmeasured(self) -> bool:
        """No graded sample, and the reason is non-measurement rather than
        outage. A case with one ERROR among its silences is NOT this — that run
        had something go wrong in it and must not be waved through."""
        return bool(self.verdicts) and all(v == "UNMEASURED" for v in self.verdicts)


#: The expectations that mean «do nothing», per suite.
QUIET: frozenset[str] = frozenset({"none", "silent"})

#: Expectations that do NOT label the decision, so they belong to neither rate:
#: counting `either` as quiet would invent false positives out of every message
#: it approves of, and counting it as positive would credit recall to a case
#: that never asked her to act.
UNLABELLED_DECISION: frozenset[str] = frozenset({"either"})


def act_case_ok(tally: CaseTally) -> bool:
    """Does this act case let the gate stay green?

    The ONE place the act suite's verdict is decided, extracted out of
    `evals.run` so it can be pinned by a unit test — it is the function that
    flips PASS/FAIL and it had no guard.

    - Any `FAIL` sample is fatal on EVERY expectation. On `silent` that is one
      speak in N: the room heard it, and a majority cannot un-hear it. On
      `either` it is one message carrying her own bookkeeping, which is the
      same argument about the same room.
    - `speak` binds on the majority: one quiet sample out of five is the
      model's threshold, not a defect.
    - `either` with no graded sample at all is UNMEASURED, and UNMEASURED is
      not a failure. This case's YAML declares silence defensible on this
      input; reddening the gate because she took the option we ourselves
      labelled defensible would be a lie about what went wrong. It is not a
      silent pass either: `summarise` counts it, `_print_proactive_gate`
      prints the count, and `_tally_line` marks the case `[UNMEASURED]`. What
      a run cannot do is read healthy while it measured nothing.
    - One ERROR among those silences and `fully_unmeasured` is False, so the
      case falls through to `majority_pass` and reddens. An outage is still an
      outage.
    """
    if "FAIL" in tally.verdicts:
        return False
    if tally.expect == "either" and tally.fully_unmeasured:
        return True
    return tally.majority_pass


def summarise(tallies: Sequence[CaseTally]) -> dict[str, Any]:
    """Over SAMPLES. `false_positive_rate` — a quiet case that acted — is
    the primary number (39 §5); `recall` is the positive cases that acted as
    expected.

    Three things are excluded from both rates and reported apart, and they are
    three DIFFERENT things: an outage (`errors`) is not a miss and not a false
    positive; a non-measurement (`unmeasured`) is not an outage; and an
    `either` case belongs to neither rate at all, because it never labelled a
    decision to be right or wrong about."""
    quiet_graded = quiet_acted = 0
    positive_graded = positive_hit = 0
    for t in tallies:
        if t.expect in UNLABELLED_DECISION:
            continue
        for v in t.verdicts:
            if v in ("ERROR", "UNMEASURED"):
                continue
            if t.expect in QUIET:
                quiet_graded += 1
                quiet_acted += v == "FAIL"
            else:
                positive_graded += 1
                positive_hit += v == "PASS"
    return {
        "cases": len(tallies),
        "quiet_cases": sum(1 for t in tallies if t.expect in QUIET),
        "message_only_cases": sum(1 for t in tallies if t.expect in UNLABELLED_DECISION),
        "majority_pass": sum(1 for t in tallies if t.majority_pass),
        "unmeasured_cases": sum(1 for t in tallies if t.fully_unmeasured),
        "samples": sum(len(t.verdicts) for t in tallies),
        "errors": sum(t.errors for t in tallies),
        "unmeasured": sum(t.unmeasured for t in tallies),
        "false_positive_rate": (quiet_acted / quiet_graded) if quiet_graded else None,
        "recall": (positive_hit / positive_graded) if positive_graded else None,
        "verdicts": dict(Counter(v for t in tallies for v in t.verdicts)),
    }


def _distinct(cases: Sequence[Any]) -> None:
    names = [c.name for c in cases]
    if len(set(names)) != len(names):
        raise ValueError("case names must be distinct")
