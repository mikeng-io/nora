"""What code hands a Cognition session to JUDGE from.

**The failure this exists for.** An agenda line is a count: «room R has 23 new
messages». A session handed only that has nothing to tell a plan from a joke,
cannot cite a message id in a note (a body line MUST end in the ids it rests on,
`nora.domain.note_body`) and cannot see what is already recorded, so it writes
a duplicate or closes `no_change` on a room it never read. AGENTS.md's spine
says it once for every model that judges: the input has to explain itself, and
a judgement whose input cannot ground it does not fail loudly, it answers
wrong at a rate nothing reports. So code hands it, for
each item, the Record rows that raised its stimuli (author, room, id,
time and, when the room said something with a photo in it, what the photo
shows), and the index lines of the Register rows the session would otherwise
duplicate or contradict. Index lines, never bodies: a body is one read
tool away, and a pushed body is a disclosure decision nobody made.

**Interpret once.** `Grounding` is built once per session by the caller and
handed, whole, to the pure renderer (`nora.language.cognition_render`); nothing
downstream re-reads a row. It is also what the session row records, so a
session is replayable from what it was actually shown.

**What is never in it** (each has a test): a message the withdrawal cascade
hid (`nora person forget` (removed), the Record read excludes it), the words of a
message its author deleted (the row is shown as deleted, like `record_query`),
and any row of a room that is not one of the unit's rooms. Framework-free, per
`nora/domain/`'s rule.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from nora.domain.cognition_session import Room
from nora.domain.culture import CultureRevision
from nora.domain.memo import Memo
from nora.domain.stimuli import Stimulus

#: The tables a message stimulus's `ref` names a row of (`nora.ingest.stages.
#: stimulus._REF_TABLE`): a message or an edit names the message, a reaction
#: and a delete name their own row.
RECORD_REF_TABLES: tuple[str, ...] = ("messages", "message_reaction", "message_delete")

RecordKind = Literal["message", "edit", "reaction", "delete"]

#: What a room with no label is called in a prompt. Never its conversation id
#: (R-18, and the deleted proactive pass's `UNNAMED_ROOM` rule): a raw id on a
#: model's wire is an identifier egress and reads as noise.
UNNAMED_ROOM = "a room with no name"


def record_ref(stimulus: Stimulus) -> tuple[str, int] | None:
    """`(table, row id)` of the Record row a `message` stimulus names, or None
    for every other kind and for a `ref` that is not one."""
    if stimulus.kind != "message":
        return None
    table, _, tail = stimulus.ref.partition(":")
    if table not in RECORD_REF_TABLES or not tail.isdigit():
        return None
    return table, int(tail)


@dataclass(frozen=True, slots=True)
class RecordItem:
    """One Record row that raised a stimulus, as the session reads it.

    `message_id` is the id a note line cites. `author` is the identity-
    canonical name (`Persons.name_transcript`), falling back to the self-set
    profile name, and never a platform id (R-18). `text` is None when the words
    are not shown: the author deleted the message. A `reaction` names its
    emoji and the message it is on; nobody's account is named for a reaction
    (the Record holds the sender's platform id, not a person)."""

    ref: str
    kind: RecordKind
    message_id: int
    room: Room
    author: str
    at_ms: int
    text: str | None
    emoji: str = ""
    edited: bool = False
    #: The stimulus's person id (or None), what the session row keeps in the
    #: NAME's place: `cognition_session.situation` is append-only and
    #: `nora person forget` (removed) cannot rewrite it, so a canonical name written
    #: there would outlive the person's withdrawal. A name is rendered to the
    #: model at read time and never serialized.
    person_id: str | None = None
    #: Nora's OWN send in the item's window (D1, DEC-245): context the room is
    #: answering, marked as hers, never a stimulus and never something to cite.
    own: bool = False


@dataclass(frozen=True, slots=True)
class RollupRef:
    """What a Rollup stimulus's `ref` and `item` say, parsed (the producers are
    `nora.analytics.units.UnitRollups._raise`): the row's kind, the person ids
    it names, its interval and its place. `end_ms` is None for a co-location
    still open. Person ids are the roster's, never a platform id."""

    kind: str
    person_ids: tuple[str, ...]
    start_ms: int | None
    end_ms: int | None
    place: str | None = None


def parse_rollup_ref(stimulus: Stimulus) -> RollupRef | None:
    """The ref's fields, or None for a stimulus that is not a Rollup one or
    whose `ref` is not one of the three shapes its producer writes:

    - `colocation|<person>,<person>|<since ms>`
    - `arrival|<place ref>|<person>|<since ms>`
    - `deviation|<anomaly or silence>|<person>|<from ms>` (it ends at the
      stimulus's own `at_ms`)

    Unreadable is None, never a guess: a session is not handed a rollup row it
    cannot read (DEC-245)."""
    if stimulus.kind != "rollup":
        return None
    parts = stimulus.ref.split("|")
    try:
        if parts[0] == "colocation" and len(parts) == 3:
            people = tuple(p for p in parts[1].split(",") if p)
            return RollupRef("colocation", people, int(parts[2]), None) if people else None
        if parts[0] == "arrival" and len(parts) == 4 and parts[2]:
            return RollupRef("arrival", (parts[2],), int(parts[3]), None, place=parts[1] or None)
        if parts[0] == "deviation" and len(parts) == 4 and parts[1] in ("anomaly", "silence") \
                and parts[2]:
            return RollupRef(
                f"deviation_{parts[1]}", (parts[2],), int(parts[3]), stimulus.at_ms,
            )
    except ValueError:
        return None
    return None


RollupStatus = Literal["readable", "roster_miss", "malformed"]


def rollup_status(stimulus: Stimulus, known: Callable[[str], bool]) -> RollupStatus:
    """Whether a session can READ a Rollup stimulus, decided from the stimulus
    and the roster alone, never from what a grounder happened to build (DEC-245).

    `malformed`: the ref is not one of the three shapes; it can never be read.
    `roster_miss`: it parses but names a person `known` does not: readable once
    the roster knows them, so it is retained, not consumed. `readable`
    otherwise. A rollup cut by `max_items` is `readable` (it is DEFERRED, like
    any item), which is the case the earlier grounder-keyed test lost."""
    parsed = parse_rollup_ref(stimulus)
    if parsed is None:
        return "malformed"
    if not all(known(p) for p in parsed.person_ids):
        return "roster_miss"
    return "readable"


@dataclass(frozen=True, slots=True)
class StayDeviation:
    """One way a stay differed from the person's own past
    (`nora.domain.mobility_forecast.Anomaly`, computed on read): its `kind`,
    and `observed`/`expected` in that kind's unit (milliseconds for
    `duration`, local minute of day for `arrival`/`departure`, None for the
    two place kinds). `usual_place` is the words for where they usually are at
    these hours (`unusual_place` only)."""

    kind: str
    observed: float | None = None
    expected: float | None = None
    usual_place: str | None = None


@dataclass(frozen=True, slots=True)
class RollupLine:
    """A Rollup stimulus as the session reads it: the `ref`'s fields with the
    people's roster NAMES. Built at read time and never serialized (a name in
    an append-only row outlives a person's withdrawal).

    For a `deviation_anomaly`, `stay_place` (what she calls the place of the
    stay) and `deviations` (how it differed from what is usual for them) are
    read from the presence store when it has them (DEC-279), and are words
    built at read time too: neither reaches the session row."""

    ref: str
    kind: str
    names: tuple[str, ...]
    person_ids: tuple[str, ...]
    start_ms: int | None
    end_ms: int | None
    place: str | None = None
    stay_place: str | None = None
    deviations: tuple[StayDeviation, ...] = ()


@dataclass(frozen=True, slots=True)
class NoteLine:
    """An open note's index line: its id (what `note_update` takes), title and
    one-line description, never its body. `review` and `may_duplicate` are
    code's facts for the notebook review a `time` item asks for: the anchor
    day has passed or the revisit date has come (`anchor_passed`,
    `revisit_due`), and the id of another open note whose title shares most of
    its words. Facts to read, never a verdict: whether the note is settled or a
    duplicate is the session's judgement."""

    id: str
    title: str
    description: str
    review: str | None = None
    may_duplicate: str | None = None
    #: Her own pending reminders linked to this note, `<id> due <when>` each:
    #: what `remind_move` and `remind_cancel` take. Without it a session that
    #: reads the room settling the thing cannot see the ask it already queued.
    reminds: str | None = None


@dataclass(frozen=True, slots=True)
class EventLine:
    """An open (proposed) event's index line."""

    id: str
    room: Room
    title: str
    when_ms: int | None
    venue: str
    status: str
    revision: str | None = None


@dataclass(frozen=True, slots=True)
class Grounding:
    """Everything one session is shown beside its agenda, built once.

    `records` is keyed by the stimulus `ref` (`messages:12`), so the renderer
    joins a stimulus to its row without re-parsing anything. `notes` is the
    unit's (the unit is the read boundary, DEC-231); `events` are the rooms on
    the agenda's. There is no Standing here: Standing is pulled, never pushed,
    and `standing_read` does not exist yet."""

    records: Mapping[str, RecordItem] = field(default_factory=dict)
    notes: tuple[NoteLine, ...] = ()
    #: The unit's kept memos (DEC-306), unit-wide like `notes`: the `memos`
    #: index, titles and ids, never what they keep.
    memos: tuple[Memo, ...] = ()
    events: tuple[EventLine, ...] = ()
    #: A Rollup stimulus's readable fields, by the stimulus `ref`. A rollup
    #: stimulus with no entry here cannot be shown, and is not an agenda item.
    rollups: Mapping[str, RollupLine] = field(default_factory=dict)
    #: Nora's own sends inside each item's window, by the item's index in the
    #: agenda that was grounded (D1): what the room's messages may be answering.
    own_sends: Mapping[int, tuple[RecordItem, ...]] = field(default_factory=dict)
    #: `platform:conversation_id` -> the room's label. The prompt says the
    #: label; the id stays where a tool's argument needs it.
    room_labels: Mapping[str, str] = field(default_factory=dict)
    #: Her own Character · Nora for the unit, rendered
    #: as the `self` block; `None` when the unit has no account or the read
    #: failed.
    self_account: CultureRevision | None = None

    def as_dict(self) -> dict[str, object]:
        """The JSON the session row keeps: what she was shown, by reference
        (a message id, a note id), not the bodies and NEVER a name: the row is
        append-only, and a person's forget must not leave their canonical name
        in it."""
        return {
            "records": [
                {"ref": r.ref, "kind": r.kind, "message_id": r.message_id,
                 "platform": r.room.platform, "conversation_id": r.room.conversation_id,
                 "person_id": r.person_id, "at_ms": r.at_ms, "words_shown": r.text is not None}
                for r in self.records.values()
            ],
            "notes": [n.id for n in self.notes],
            "memos": [m.id for m in self.memos],
            # By revision id, never the words: the account is hers to rewrite.
            "self_revision_id": None if self.self_account is None else self.self_account.id,
            "events": [e.id for e in self.events],
            # By reference, and no name: a person id, the kind, the interval.
            "rollups": [
                {"ref": r.ref, "kind": r.kind, "person_ids": list(r.person_ids),
                 "start_ms": r.start_ms, "end_ms": r.end_ms, "place": r.place,
                 "deviations": [d.kind for d in r.deviations]}
                for r in self.rollups.values()
            ],
            # Her own sends by message id and time; never the words.
            "own_sends": {
                str(i): [{"message_id": m.message_id, "at_ms": m.at_ms,
                          "words_shown": m.text is not None} for m in sends]
                for i, sends in self.own_sends.items()
            },
        }


__all__ = [
    "RECORD_REF_TABLES", "UNNAMED_ROOM", "EventLine", "Grounding", "NoteLine", "RecordItem",
    "RecordKind", "RollupLine", "RollupRef", "StayDeviation", "parse_rollup_ref", "record_ref",
]
