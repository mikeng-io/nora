"""The Cognition adaptor's session and agenda, built by CODE, before any
model call.

**Interpret once (AGENTS.md's CMS discipline).** A unit's pending stimuli
(`nora.domain.stimuli.Stimulus`, already read by the wake evaluator) are
turned into a fixed `Situation` and `Agenda` exactly once, here, by pure
functions. Everything downstream — the write verbs a session
calls, the assess pass that reads the session row back — computes on that
fixed model and never re-reads the raw stimulus rows itself; the session
row (`nora.infra.sql.cognition_session`) serializes it with provenance so a
later reader does not have to trust the session that built it.

**One item per thing that needs attention**: an item states what
happened, never what to conclude from it, and never a menu of possible
actions — `build_agenda` below only ever writes a fact ("room R has N new
messages", "a rollup row changed for <ref>"), computed from the stimuli, and
raises no judgement. Code orders items by a deterministic **surprise**
score (a strong cue, a rollup/self/time kind, and volume, in that order of
weight; every weight here is a starting guess) and hands at most
`max_items` (default 12); the rest wait for the next session, unconsumed
(`build_agenda` returns them separately so the caller's watermark advance can
exclude what it never saw — see `Agenda.deferred`).

**Every item binds one room.** A `message` stimulus is bound to the room
it came from (R-21); every other kind (`social`, `play`, `rollup`, `time`,
`self`) has no room of its own and is bound to the unit's HOME room, except
the daily look ahead (`UPCOMING_ITEM`, a `time` stimulus that names the room
its event is in, and is bound to it). `home_room` is a required argument
for exactly this reason: this module does not resolve it (that is `policy.toml`'s unit
configuration, a concern of the caller wiring the session, not of this pure
builder).

This module makes no store call and no model call: it is a function of the
`Stimulus` rows the caller already read (`StimulusStore.view`, the same read
the wake evaluator makes) and the caller's own `now_ms`/`home_room`/
`last_session_ms`. Nothing here can write or send — see
`tests/architecture/test_the_cognition_session_module_cannot_act.py`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from nora.domain.stimuli import Stimulus

#: The Dreamer adaptor name a Cognition session's tools are built under
#: (`RegistryToolSource.build(adaptor=...)` stamps it as `dream_adaptor`). A
#: read tool that would WRITE something a live reader consumes checks it and
#: does not (`nora.agent.tools.note_search`): the session's ceiling is «may
#: look, may not write».
COGNITION_ADAPTOR = "cognition"

#: The kinds bound to the room they name; every other kind binds the unit's
#: home room. A message names the room it came from; a social read names a
#: room of the unit that lists its author, the only rooms that may be shown
#: it (`composition.social_person_rooms`), and one naming none binds home.
_ROOM_BOUND_KINDS: frozenset[str] = frozenset({"message", "social"})
#: The daily look ahead (DEC-303): a `time` stimulus the wake evaluator raises
#: with each floor for a room holding an event that is coming up. Unlike the
#: floor it names a room, and is bound to it, so the look reads and writes
#: where the event is.
UPCOMING_ITEM = "upcoming"

#: An item closes with a write that cites it, or with one of these reasons;
#: `struck` is code's own close at
#: `max_strikes`, never a session's argument.
CLOSE_REASONS: tuple[str, ...] = ("written", "no_change", "waiting", "covered", "struck")

#: The default `max_items`.
DEFAULT_MAX_ITEMS = 12

#: The default `max_strikes`.
DEFAULT_MAX_STRIKES = 3


@dataclass(frozen=True, slots=True)
class Room:
    """A conversation key — the same tuple every store keys a room by."""

    platform: str
    conversation_id: str


@dataclass(frozen=True, slots=True)
class AgendaItem:
    """One thing that needs attention, computed from one or more stimuli of
    the same kind bound to the same room. `summary` states what happened,
    never what to conclude, enforced here by
    construction: `_summary` below never reads a stimulus's cues into a
    conclusion, only into a count.

    `strike` is the session's own count on THIS item (0 on its first
    appearance); a caller that re-derives an item still open from a prior
    session's close row passes the strike it read forward."""

    kind: str
    room: Room
    summary: str
    stimulus_ids: tuple[int, ...]
    cues: tuple[str, ...]
    surprise: float
    strike: int = 0

    def as_dict(self) -> dict[str, object]:
        """The JSON shape written to `cognition_session.agenda` — the
        session row's own provenance, not a display format."""
        return {
            "kind": self.kind,
            "platform": self.room.platform,
            "conversation_id": self.room.conversation_id,
            "summary": self.summary,
            "stimulus_ids": list(self.stimulus_ids),
            "cues": list(self.cues),
            "surprise": self.surprise,
            "strike": self.strike,
        }


@dataclass(frozen=True, slots=True)
class Agenda:
    """The ordered items one session gets, and what it does not: `deferred`
    holds the stimulus ids of items surprise-ranked past `max_items`, which
    the caller must NOT fold into its watermark advance (the rest wait for the
    next session)."""

    items: tuple[AgendaItem, ...]
    deferred_stimulus_ids: tuple[int, ...] = ()

    @property
    def is_empty(self) -> bool:
        """An agenda with no items is no session (zero model calls): the caller's
        own gate; this property is what it reads."""
        return len(self.items) == 0


@dataclass(frozen=True, slots=True)
class Situation:
    """What code hands the session before any model call: the unit, its clock, how long since its
    last session, and the agenda. Deliberately holds no Register/Character/Standing BODIES — those
    are index lines and capped pushed blocks the caller assembles
    separately; this module only ever sees stimuli."""

    unit: str
    now_ms: int
    last_session_ms: int | None
    agenda: Agenda

    def as_dict(self) -> dict[str, object]:
        return {
            "unit": self.unit,
            "now_ms": self.now_ms,
            "last_session_ms": self.last_session_ms,
            "agenda": [item.as_dict() for item in self.agenda.items],
        }


def _room_of(stimulus: Stimulus, *, home_room: Room) -> Room:
    if (
        (stimulus.kind in _ROOM_BOUND_KINDS
         or (stimulus.kind == "time" and stimulus.item == UPCOMING_ITEM))
        and stimulus.platform is not None
        and stimulus.conversation_id is not None
    ):
        return Room(stimulus.platform, stimulus.conversation_id)
    return home_room


def _group_key(stimulus: Stimulus, *, home_room: Room) -> tuple[str, Room, str]:
    """One agenda item per (kind, room, item) — `item` is `Stimulus.item`
    (e.g. the rollup subject, the `self`-wake's id, the day's floor label),
    the same free-text key `nora.app.wake` already groups would-wakes'
    `detail.items` by. `message`/`social`/`play` share ONE item per room
    regardless of `item`, because the agenda states "room R has 23 new
    messages", not one item per message."""
    if stimulus.kind in ("message", "social", "play"):
        return (stimulus.kind, _room_of(stimulus, home_room=home_room), "")
    return (stimulus.kind, _room_of(stimulus, home_room=home_room), stimulus.item)


def _summary(kind: str, room: Room, item: str, count: int) -> str:
    """A fact, never a conclusion. No branch here reads
    a cue's CONTENT, only counts — reading a cue's meaning into prose is the
    model's job, not code's."""
    if kind == "message":
        noun = "message" if count == 1 else "messages"
        # No room in the sentence: the item's `room` attribute says which, by its
        # label. The summary is prompt text and a conversation id must never
        # reach a prompt (R-18; the label is the room's name).
        return f"{count} new {noun} in this room"
    if kind == "social":
        noun = "item" if count == 1 else "items"
        return f"{count} new social {noun}"
    if kind == "play":
        noun = "sample" if count == 1 else "samples"
        return f"{count} new play {noun}"
    if kind == "rollup":
        return f"a rollup row changed: {item}"
    if kind == "time":
        if item == "floor":
            return ("the daily look at the notebook: the note index marks the notes "
                    "whose day has passed, whose revisit has come, or that may be "
                    "duplicates")
        if item == UPCOMING_ITEM:
            return ("the daily look ahead: this room has an event coming up, listed "
                    "under its events; no event reminds anyone by itself")
        return f"a time stimulus is due: {item}"
    if kind == "self":
        return f"a self-wake is due: {item}"
    return f"{count} {kind} stimuli: {item}"


def _surprise(kind: str, cues: tuple[str, ...], count: int) -> float:
    """A deterministic score; every weight is a starting guess. Higher wins."""
    score = 0.0
    if cues:
        score += 10.0
    if kind in ("rollup", "self"):
        score += 5.0
    if kind == "time":
        score += 3.0
    score += min(count, 5)
    return score


def build_agenda(
    pending: Sequence[Stimulus], *, home_room: Room, max_items: int = DEFAULT_MAX_ITEMS,
    strikes: Mapping[int, int] | None = None,
) -> Agenda:
    """Group `pending` into agenda items, order by surprise (ties broken by
    the group's oldest stimulus id, so the ordering is a pure function of its
    input — no dict-iteration-order dependency), and cap at `max_items`.
    `strikes` maps a stimulus id a CLOSED session returned to this agenda onto
    its strike (`UnitView.carry`). A strike belongs to an ITEM: the
    strike is part of the grouping key, so a returning item stays its own item
    with its own count and a fresh stimulus of the same room never joins it
    (it would inherit a strike it never earned, and be struck with it). The
    caller passes `pending` as the stimuli one wake claims (its
    `through_stimulus_id` range, already computed by
    `nora.domain.stimuli.decide`); this function claims nothing itself.
    """
    carried = strikes or {}
    groups: dict[tuple[str, Room, str, int], list[Stimulus]] = {}
    for stimulus in pending:
        key = (*_group_key(stimulus, home_room=home_room), carried.get(stimulus.id, 0))
        groups.setdefault(key, []).append(stimulus)

    items: list[AgendaItem] = []
    for (kind, room, item_key, strike), rows in groups.items():
        ids = tuple(sorted(s.id for s in rows))
        cues = tuple(sorted({cue for s in rows for cue in s.cues}))
        summary = _summary(kind, room, item_key, len(rows))
        surprise = _surprise(kind, cues, len(rows))
        items.append(AgendaItem(
            kind=kind, room=room, summary=summary,
            stimulus_ids=ids, cues=cues, surprise=surprise, strike=strike,
        ))

    # Order by (surprise desc, oldest stimulus id asc): deterministic.
    items.sort(key=lambda item: (-item.surprise, item.stimulus_ids[0]))

    kept = tuple(items[:max_items])
    deferred = tuple(
        sid for item in items[max_items:] for sid in item.stimulus_ids
    )
    return Agenda(items=kept, deferred_stimulus_ids=tuple(sorted(deferred)))


def build_situation(
    *, unit: str, pending: Sequence[Stimulus], home_room: Room, now_ms: int,
    last_session_ms: int | None, max_items: int = DEFAULT_MAX_ITEMS,
    strikes: Mapping[int, int] | None = None,
) -> Situation:
    """The one entry point: `pending` in, `Situation` out, no store, no
    model. `nora.app.cognition_session` (the async half) is the only intended caller in
    production; every other caller in this tree is a test."""
    agenda = build_agenda(
        pending, home_room=home_room, max_items=max_items, strikes=strikes,
    )
    return Situation(
        unit=unit, now_ms=now_ms, last_session_ms=last_session_ms, agenda=agenda,
    )


@dataclass(frozen=True, slots=True)
class CloseOutcome:
    """One agenda item's close. `reason='written'` is
    code's own classification when the session's write cited the item — this
    module does not decide that; the caller (which sees the write verbs
    actually called) does and passes the reason in."""

    item_index: int
    kind: str
    reason: str
    strike: int = 0

    def __post_init__(self) -> None:
        if self.reason not in CLOSE_REASONS:
            raise ValueError(f"not a close reason: {self.reason!r}")


def close_or_strike(
    item: AgendaItem, *, item_index: int, reason: str,
    max_strikes: int = DEFAULT_MAX_STRIKES,
) -> CloseOutcome:
    """An item left open when the session ends, or closed `waiting`, returns
    to the next agenda with a strike; at `max_strikes` code closes it
    `struck`. `reason='written'`/`'no_change'`/
    `'covered'` never strike (they close outright); `'waiting'` (or no close
    at all, which the caller passes as `'waiting'`) strikes, and code
    escalates to `'struck'` once `item.strike + 1 >= max_strikes`.

    `item_index` is the item's position in the agenda that was HANDED to the
    session (not recomputed here — an index is only meaningful against the
    list it came from, and this function does not hold that list)."""
    if reason not in ("written", "no_change", "waiting", "covered"):
        raise ValueError(f"not a session close reason: {reason!r}")
    if reason != "waiting":
        return CloseOutcome(
            item_index=item_index, kind=item.kind, reason=reason, strike=item.strike,
        )
    next_strike = item.strike + 1
    if next_strike >= max_strikes:
        return CloseOutcome(
            item_index=item_index, kind=item.kind, reason="struck", strike=next_strike,
        )
    return CloseOutcome(
        item_index=item_index, kind=item.kind, reason="waiting", strike=next_strike,
    )
