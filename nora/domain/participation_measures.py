"""The four measures that grade the ambient path against something other than
its own opinion.

**Why this module exists.** Today the verdict IS the measurement: the
participation path counts what a model said about an instruction someone wrote,
and `proactive_decide` does the same. A model agreeing with its own instruction
is not evidence about the room. Worse, a `no_go` is unfalsifiable by
construction — she stays silent, nothing happens, and silence is also what
success looks like — so a refusal is indistinguishable from health in every log
the system keeps.

**None of these four defines when she should intervene, and none needs a
model.** They are arithmetic over rows the rooms produce anyway. Two are
labelled by her own state and two by human behaviour, on purpose: a pair that
could only be wrong together would be self-attesting, which is the thing being
escaped.

| measure | labelled by | shape |
|---|---|---|
| `expiry_without_action` | her own store | a note passed its anchor still open, never raised |
| `holding_refusals` | her own store | the gate refused room R while she held something for R |
| `overrides` | the room | a human tagged her in R shortly after a `no_go` |
| `ignored` | the room | she spoke unprompted and the next human line did not engage |

**Every instant in this module is EPOCH MILLISECONDS**, because that is what
`messages.ts`, `decision_log.ts`, `note.created_at` and `note.retired_at` are.
The one exception in the schema is `note.anchor_date`, which is a `YYYYMMDD`
CALENDAR DAY and not epoch anything — it is converted at the boundary by
`anchor_bounds_ms`, and doing that conversion is not optional: comparing a
raw `20260914` against a `now` of `1_789_...` silently classifies EVERY
anchored note as expired.

**Everything here is pure.** Inputs are rows; there is no store, no clock and no
I/O. `nora.app.participation_measures` gathers; this decides. The split is not
tidiness: `refusals` arrives from a DIFFERENT and much weaker instrument than
everything else (see `Refusal`), and a function that fetched its own inputs
could not be handed a better one when it lands.

**Every threshold here is a placeholder.** The 600-second windows are not
measured and cannot be until these measures have run long enough to have a
distribution. They are arguments, not constants, so a caller can sweep them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from nora.domain.notes import anchor_bounds_ms

__all__ = [
    "DecisionLike",
    "Measure",
    "MessageLike",
    "NoteLike",
    "Observation",
    "Refusal",
    "Spoke",
    "Tag",
    "expiry_without_action",
    "holding_refusals",
    "ignored",
    "overrides",
]

class NoteLike(Protocol):
    """The four fields of a `Note` these measures read.

    A Protocol and not `nora.domain.notes.Note` so this module stays testable
    against a two-line stub, and so a store that grows a field cannot quietly
    change what a measure means.
    """

    @property
    def id(self) -> str: ...
    @property
    def established_in(self) -> str: ...
    @property
    def created_at(self) -> int: ...
    @property
    def anchor_date(self) -> int | None: ...
    @property
    def retired_at(self) -> int | None: ...


class DecisionLike(Protocol):
    """The two fields of a `DecisionRow` `expiry_without_action` reads."""

    @property
    def note_id(self) -> str | None: ...
    @property
    def verdict(self) -> str: ...


class MessageLike(Protocol):
    """The four fields of a stored message `ignored` reads.

    `quoted_ts` is the join that makes «engaged» decidable without a model: it
    is the ts of the message being quoted, so it matches `Spoke.at` exactly
    when the room replied TO HER TURN rather than merely after it.
    """

    @property
    def id(self) -> str: ...
    @property
    def conversation_id(self) -> str: ...
    @property
    def ts(self) -> int: ...
    @property
    def quoted_ts(self) -> int | None: ...


#: Default nearness for "shortly after", in EPOCH MILLISECONDS like everything
#: else here — 10 minutes. From the founding anecdote: a `no_go`, then 34
#: seconds later the member gave up and tagged her, then a correct answer.
#:
#: Named `_MS` rather than taking seconds and converting, because a second
#: conversion is a second place to be wrong and this module has already been
#: wrong about units once (see the module docstring). UNMEASURED.
NEAR_MS = 600_000


@dataclass(frozen=True, slots=True)
class Refusal:
    """One gate refusal, at a time, attributable to a room.

    `provenance` is a first-class field because the two sources are not
    interchangeable and a reader must not have to guess which one produced a
    number:

    - `"counter"` — reconstructed from `nora_relevance_gate_total`'s positive
      deltas. A delta at time *t* means AT LEAST ONE refusal in the scrape
      interval ending at *t*, never exactly one, so counts derived from it are
      lower bounds and times are accurate only to that interval. It is also
      lossy in a way no summation repairs: Prometheus labels lazily, the
      process restarts, and a refusal that is never scraped before a restart
      leaves no trace at all.
    - `"row"` — a durable per-verdict row. Does not exist: DEC-179 excluded the
      gate from `decision_log` deliberately. This module does not reopen that;
      it names the shape so that when one lands, nothing here changes.

    A measure fed `"counter"` refusals is APPROXIMATE and says so in its
    `ceiling`. That is not a disclaimer — it is the difference between the two
    measures that could grade a refusal and the two that cannot.
    """

    conversation_id: str
    at: int
    provenance: Literal["counter", "row"] = "counter"


@dataclass(frozen=True, slots=True)
class Tag:
    """A human addressed her in a room, at a time.

    From `decision_log` where `writer = "tag"` — a row exists whether she
    replied or failed, which is what makes it a record of the ROOM's act rather
    than of her success at answering it.
    """

    conversation_id: str
    at: int


@dataclass(frozen=True, slots=True)
class Spoke:
    """She spoke unprompted in a room, at a time.

    From `decision_log` where `writer = "participation"` and `verdict =
    "spoke"`. Unprompted is carried by the writer, not inferred: the tag path
    writes `writer = "tag"`, so there is nothing to disentangle.

    **`at` is the ts of the MESSAGE she sent, not the ts of the decision row.**
    They are close but not equal, and `ignored` decides engagement by matching
    a reply's `quoted_ts` against this value exactly — fed the decision's ts it
    would match nothing and report every turn as ignored. Joining the two is
    the gatherer's job and is the one place this measure can be wired wrongly
    and still look plausible.
    """

    conversation_id: str
    at: int


@dataclass(frozen=True, slots=True)
class Observation:
    """One instance a measure found, with the two facts that make it checkable:
    which room, and when."""

    conversation_id: str
    at: int
    subject: str
    """What was found — a note id, or the id of the thing she said. Never
    room content: these rows are read by an operator and may be pasted."""


@dataclass(frozen=True, slots=True)
class Measure:
    """A measure's whole answer, INCLUDING what it structurally cannot see.

    `ceiling` is a required field and not a docstring, because every one of
    these four has a blind spot that changes how its number should be read, and
    a number that travels without it gets read as a rate. `considered` is the
    denominator: `len(hits) / considered` is the only ratio these support, and
    `considered == 0` means the measure did not run rather than that it found
    nothing.
    """

    name: str
    hits: tuple[Observation, ...]
    considered: int
    ceiling: str

    @property
    def rate(self) -> float | None:
        """`None` when nothing was considered — never 0.0, which would read as
        «measured, and it never happens»."""
        return len(self.hits) / self.considered if self.considered else None


def expiry_without_action(
    notes: Sequence[NoteLike],
    decisions: Sequence[DecisionLike],
    *,
    now: int,
) -> Measure:
    """A note passed its `anchor_date` while still open, and she never raised it.

    `now` is EPOCH MILLISECONDS. `anchor_date` is NOT — it is a `YYYYMMDD`
    calendar day, converted here by `anchor_bounds_ms`, and a note counts as
    passed only once the LAST instant of that day is behind us: an arrangement
    for today is not missed at 00:01.

    Her own store labels this one: no human has to do anything for it to fire,
    which is what makes it the only one of the four that is blind to nobody.

    "Raised it" is a `proactive_decide` row with `verdict = "remind"` naming the
    note, at any time before the anchor passed. A `decline` is NOT raising it —
    that is the decision this measure exists to grade.

    **Notes with no `anchor_date` are not considered** (the field is optional —
    a living thread has no date). They are not failures and not successes; they
    are outside this instrument, and folding them into the denominator would
    dilute the rate with rows that could never fire.
    """
    reminded = {d.note_id for d in decisions if d.verdict == "remind" and d.note_id}

    hits: list[Observation] = []
    considered = 0
    unreadable = 0
    for n in notes:
        if n.anchor_date is None:
            continue
        bounds = anchor_bounds_ms(n.anchor_date)
        if bounds is None:
            # A stored value that is not a date this can read. Counted and
            # reported rather than skipped silently: it is a note whose anchor
            # can never fire, which looks identical to a note that has not
            # expired yet.
            unreadable += 1
            continue
        passed_at = bounds[1]  # the LAST instant of the day; it is not late until the day is over
        if passed_at > now:
            continue
        considered += 1
        if n.retired_at is not None or n.id in reminded:
            continue
        hits.append(Observation(
            conversation_id=n.established_in, at=passed_at, subject=n.id,
        ))
    return Measure(
        name="expiry_without_action",
        hits=tuple(sorted(hits, key=lambda o: (o.at, o.subject))),
        considered=considered,
        ceiling=(
            "Blind to notes with no anchor_date, which are excluded rather than "
            "counted. Says nothing about whether raising it would have helped — "
            "only that the moment passed unremarked."
            + (f" {unreadable} note(s) carry an anchor_date this cannot read as "
               "a date; their anchors can never fire and they are excluded."
               if unreadable else "")
        ),
    )


def holding_refusals(
    refusals: Sequence[Refusal],
    notes: Sequence[NoteLike],
) -> Measure:
    """The gate refused room R while she held an open note born in R.

    **The note arm only.** The intended definition included «or the room's
    `standing` records an unmet commitment», and that arm DOES NOT COMPUTE:
    `standing` is `body TEXT` plus a length CHECK, with no field, flag or
    structure for a commitment. Computing it needs a regex over
    free Cantonese or a model, and a model re-imports the exact proxy failure
    these four exist to escape.

    That matters more than an omission usually would, because
    `relevance_gate.py` makes `standing` the ONLY place that can establish what
    she owes a room — so the standing arm IS the interesting half, and it is
    the half that is missing. Reported wherever this number is reported.

    **Held FOR R means born in R.** A note is unit-scoped and
    `established_in` is a routing key rather than a filter, so in principle
    every room in the unit shares it. The narrower reading is used because
    the wider one counts a note answered in a room it was not born in, on a
    different platform. Where she would raise it
    is where she can be said to be holding it.
    """
    open_forever = 1 << 62  # a note with no `retired_at` is open, not expired
    by_room: dict[str, list[tuple[int, int, str]]] = {}
    for n in notes:
        if not n.established_in:
            continue
        closed = n.retired_at if n.retired_at is not None else open_forever
        by_room.setdefault(n.established_in, []).append((n.created_at, closed, n.id))

    hits: list[Observation] = []
    for r in refusals:
        for born, closed, note_id in by_room.get(r.conversation_id, ()):
            if born <= r.at < closed:
                hits.append(Observation(
                    conversation_id=r.conversation_id, at=r.at, subject=note_id,
                ))
    approximate = any(r.provenance == "counter" for r in refusals)
    return Measure(
        name="holding_refusals",
        hits=tuple(sorted(hits, key=lambda o: (o.at, o.subject))),
        considered=len(refusals),
        ceiling=(
            "THE NOTE ARM ONLY — the standing arm does not compute (#222), and "
            "it is the arm that carries what she owes a room. Blind to lookupable "
            "world facts, which she does not hold at all and which the one "
            "measured admission in four days happened to be."
            + (
                " Refusals came from a COUNTER: times are accurate only to the "
                "scrape interval, counts are lower bounds, and a refusal never "
                "scraped before a restart left no trace."
                if approximate else ""
            )
        ),
    )


def overrides(
    refusals: Sequence[Refusal],
    tags: Sequence[Tag],
    *,
    near_ms: int = NEAR_MS,
) -> Measure:
    """A human tagged her in R within `near_ms` after the gate refused R.

    The room labels this one, and the founding anecdote is an instance: a
    `no_go`, then 34 seconds later the member gave up and tagged her, then a
    correct answer. A human overruled the gate and the transcript recorded it.

    **A lower bound that can never be the true rate.** It only fires when
    somebody cares enough to tag, so most refusals are never labelled at all —
    and it is blind to the failure that matters most, which is the room quietly
    giving up.

    Each refusal is counted at most once even if several tags follow it, and
    each tag can confirm several refusals: the question is «was this refusal
    overruled», not «how much traffic followed».
    """
    ordered = sorted(tags, key=lambda t: t.at)
    hits: list[Observation] = []
    for r in refusals:
        for t in ordered:
            if t.conversation_id != r.conversation_id:
                continue
            if r.at <= t.at <= r.at + near_ms:
                hits.append(Observation(
                    conversation_id=r.conversation_id, at=r.at, subject=str(t.at),
                ))
                break
    approximate = any(r.provenance == "counter" for r in refusals)
    return Measure(
        name="overrides",
        hits=tuple(sorted(hits, key=lambda o: (o.at, o.conversation_id))),
        considered=len(refusals),
        ceiling=(
            "A LOWER BOUND, never the true rate: it fires only when someone "
            "cares enough to tag, and is blind to the room silently giving up. "
            f"The {near_ms // 1000}s window is a placeholder and is not measured."
            + (
                " Refusals came from a COUNTER, which gives scrape resolution "
                "and loses everything a restart interrupts — so a refusal may be "
                "timed up to one scrape interval early and some are absent "
                "entirely."
                if approximate else ""
            )
        ),
    )


def ignored(
    spoke: Sequence[Spoke],
    human_messages: Sequence[MessageLike],
    tags: Sequence[Tag] = (),
    *,
    near_ms: int = NEAR_MS,
) -> Measure:
    """She spoke unprompted, and the next human line did not engage.

    **«Engage» is mechanical and deliberately narrow**: the next human message
    in that room either quotes her turn (`quoted_ts`) or is itself a tag. No
    model reads the reply, because a model reading a reply to decide whether it
    engaged is the same proxy failure one layer over — and a wrong answer there
    would be invisible, since nothing downstream would disagree with it.

    The cost of narrowness is stated rather than hidden: a room that answers
    「好呀」 without quoting is scored as ignoring her. So this OVER-reports, and
    is a ceiling on how well she is doing rather than a floor.

    A turn with no following human line inside the window is NOT counted either
    way — it is not evidence of being ignored, it is evidence of a quiet room,
    and folding the two together is how a measure starts reporting the room's
    bedtime as a failure.
    """
    ordered = sorted(human_messages, key=lambda m: m.ts)
    hits: list[Observation] = []
    considered = 0
    for s in spoke:
        following = [
            m for m in ordered
            if m.conversation_id == s.conversation_id
            and s.at < m.ts <= s.at + near_ms
        ]
        if not following:
            continue
        considered += 1
        nxt = following[0]
        tagged = any(
            t.conversation_id == s.conversation_id
            and s.at < t.at <= s.at + near_ms
            for t in tags
        )
        if nxt.quoted_ts == s.at or tagged:
            continue
        hits.append(Observation(
            conversation_id=s.conversation_id, at=s.at, subject=nxt.id,
        ))
    return Measure(
        name="ignored",
        hits=tuple(sorted(hits, key=lambda o: (o.at, o.conversation_id))),
        considered=considered,
        ceiling=(
            "OVER-reports: «engage» means quoted or tagged, so a room that "
            "agrees in plain words without quoting is scored as ignoring her. "
            "A turn with no human line inside the window is excluded rather "
            "than counted — a quiet room is not a rejection. "
            f"The {near_ms // 1000}s window is a placeholder and is not measured."
        ),
    )
