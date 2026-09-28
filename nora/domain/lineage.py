"""Lineage: the `(kind, subject, origin room)` set a derived row's citations
reach down to (design/cognition-loop/10-tiers.md §3, migration step 2).

This module is the DATA half: the entry and the per-section set, how they
serialise to the `lineage` JSONB column, and the closed vocabularies the
schema's CHECKs are frozen from. The per-room predicates that decide whether a
room may be shown a section are the other half and live in
`nora.agent.lineage`, beside the live `DisclosureGuard` the `presence`
predicate re-runs (DEC-222).

**The column's shape.** One JSON object per row, section name to a list of
entries:

    {"row": [{"kind": "message", "subject": "<A>", "origin": "<A>"}]}

A Register row (a note) has one section, `row`, because 10 §3 gives it lineage
per row. A Standing row has `body` and `working_set`, the two sections today's
schema has (the sectioned Standing schema arrives at step 3); a culture row
(Character · room) has `body`, because culture is prose with no sections
(DEC-184) until the Character adaptor writes the sectioned account at step 4.

**An entry.** `kind` names the predicate; `subject` is what it is about (the
room `A` for `message:<A>`, the person for `presence`, `face`, `social`,
`play`); `origin` is the room the row that carries it was written in, so an
unknown kind can still be shown where it came from; `reads` is the lineage of
the item a `perception` or `face` entry reads (10 §3: "B passes the lineage of
the item it reads").

**Unknown is deny** (10 §3). A section whose list is missing or empty, an
entry of a kind no predicate is written for, and a malformed entry all render
only in the row's origin room, and nowhere when it has none. `UNRESOLVED` is
the one such kind this code writes on purpose: a citation that could not be
resolved (a message pruned since, a note index a fold was shown and did not
record) is stated rather than dropped, and 70 §5 says such a row "gets
origin-room-only lineage".

Framework-free, per `nora/domain/`'s rule: stdlib and dataclasses only.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

__all__ = [
    "BODY",
    "CONFIDENCES",
    "FACE",
    "KINDS",
    "LINEAGE_MODES",
    "MESSAGE",
    "PERCEPTION",
    "PLAY",
    "PRESENCE",
    "REASONING_MODES",
    "ROW",
    "SOCIAL",
    "UNRESOLVED",
    "WORKING_SET",
    "Lineage",
    "LineageEntry",
    "LineageMode",
    "LiveRoom",
    "RenderRoom",
    "RoomFacts",
    "message",
    "note_revision_lineage",
    "own_room_lineage",
    "unresolved",
]

#: The lineage kinds 10 §3 writes a per-room predicate for. Closed: a kind not
#: here has no predicate, and `nora.agent.lineage` denies it outside its
#: origin room.
MESSAGE = "message"
PRESENCE = "presence"
FACE = "face"
SOCIAL = "social"
PLAY = "play"
PERCEPTION = "perception"
KINDS: frozenset[str] = frozenset({MESSAGE, PRESENCE, FACE, SOCIAL, PLAY, PERCEPTION})

#: A citation code could not resolve. Deliberately NOT in `KINDS`: it is an
#: unknown kind, so it is shown only in the row's origin room (70 §5).
UNRESOLVED = "unresolved"

#: The section names today's schemas have (module docstring).
ROW = "row"
BODY = "body"
WORKING_SET = "working_set"

#: 10 §4's reasoning modes and §3's confidence levels, the vocabularies the
#: `reasoning` and `confidence` columns are checked against. Migration
#: `0070_lineage` writes them out rather than importing them (a migration is a
#: frozen historical statement, `0053_decision_log`'s rule).
REASONING_MODES: tuple[str, ...] = (
    "computed", "told", "deduced", "induced", "abduced", "said",
)
CONFIDENCES: tuple[str, ...] = ("low", "medium", "high")


@dataclass(frozen=True, slots=True)
class LineageEntry:
    """One `(kind, subject, origin room)`; see the module docstring."""

    kind: str
    subject: str | None = None
    origin: str | None = None
    reads: tuple[LineageEntry, ...] = ()

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "kind": self.kind, "subject": self.subject, "origin": self.origin,
        }
        if self.reads:
            out["reads"] = [r.to_json() for r in self.reads]
        return out

    @classmethod
    def from_json(cls, raw: Any, *, origin: str | None = None) -> LineageEntry:
        """Tolerant: anything that is not a well-formed entry becomes an
        `UNRESOLVED` entry at `origin`, never an exception and never a
        permissive value. A reader that raised here would take a whole block
        down over one bad row; one that skipped the entry would widen the row
        (fewer entries, fewer predicates to fail)."""
        if not isinstance(raw, Mapping):
            return cls(kind=UNRESOLVED, origin=origin)
        kind = raw.get("kind")
        subject = raw.get("subject")
        entry_origin = raw.get("origin", origin)
        reads_raw = raw.get("reads", ())
        if (
            not isinstance(kind, str) or not kind
            or (subject is not None and not isinstance(subject, str))
            or (entry_origin is not None and not isinstance(entry_origin, str))
            or not isinstance(reads_raw, list | tuple)
        ):
            return cls(kind=UNRESOLVED, origin=origin)
        return cls(
            kind=kind, subject=subject, origin=entry_origin,
            reads=tuple(cls.from_json(r, origin=entry_origin) for r in reads_raw),
        )

    def _key(self) -> tuple[Any, ...]:
        return (self.kind, self.subject or "", self.origin or "",
                tuple(r._key() for r in self.reads))


def _canonical(entries: Iterable[LineageEntry]) -> tuple[LineageEntry, ...]:
    """Deduplicated and in one order, so two rows holding the same set compare
    equal and serialise byte-identically (a set, 10 §3)."""
    unique = {e._key(): e for e in entries}
    return tuple(unique[k] for k in sorted(unique))


@dataclass(frozen=True, slots=True)
class Lineage:
    """A row's lineage: section name to its entries. Hashable on purpose (a
    tuple of pairs, not a dict), because the frozen domain rows that carry it
    (`Note`, `Standing`, `CultureRevision`) hash their fields.

    The empty `Lineage()` is every section unknown, which renders only in the
    row's origin room: the default for a row built without one, so a harness
    that constructs a row by hand is on the fail-closed side.
    """

    sections: tuple[tuple[str, tuple[LineageEntry, ...]], ...] = ()

    @classmethod
    def of(cls, sections: Mapping[str, Iterable[LineageEntry]]) -> Lineage:
        return cls(sections=tuple(
            (name, _canonical(entries)) for name, entries in sorted(sections.items())
        ))

    def section(self, name: str) -> tuple[LineageEntry, ...]:
        """The entries of `name`; `()` (unknown) when the row has none."""
        for section, entries in self.sections:
            if section == name:
                return entries
        return ()

    def union(self, other: Lineage) -> Lineage:
        """Every entry of both, section by section: what a row that rests on
        both rests on (10 §3, a set)."""
        merged: dict[str, list[LineageEntry]] = {}
        for lineage in (self, other):
            for name, entries in lineage.sections:
                merged.setdefault(name, []).extend(entries)
        return Lineage.of(merged)

    def to_json(self) -> dict[str, Any]:
        return {name: [e.to_json() for e in entries] for name, entries in self.sections}

    @classmethod
    def from_json(cls, raw: Any, *, origin: str | None = None) -> Lineage:
        """The column's value as read back. Anything that is not an object of
        lists reads as `Lineage()`: every section unknown."""
        if not isinstance(raw, Mapping):
            return cls()
        sections: dict[str, list[LineageEntry]] = {}
        for name, entries in raw.items():
            if not isinstance(name, str):
                continue
            if not isinstance(entries, list | tuple):
                sections[name] = [LineageEntry(kind=UNRESOLVED, origin=origin)]
                continue
            sections[name] = [LineageEntry.from_json(e, origin=origin) for e in entries]
        return cls.of(sections)


def message(room: str) -> LineageEntry:
    """`message:<room>`: the row rests on something said in `room`."""
    return LineageEntry(kind=MESSAGE, subject=room, origin=room)


def unresolved(origin: str | None) -> LineageEntry:
    """A citation that could not be resolved; shown only in `origin`."""
    return LineageEntry(kind=UNRESOLVED, origin=origin)


#: `[lineage] mode` (config.toml). `off`: blocks render as before and nothing
#: is computed. `shadow`: computed and reported on the prefill span, rendered
#: as before. `on`: a section that fails is not rendered.
LineageMode = Literal["off", "shadow", "on"]
LINEAGE_MODES: tuple[str, ...] = ("off", "shadow", "on")


@dataclass(frozen=True, slots=True)
class RoomFacts:
    """What `policy.toml` says about one room, as the predicates need it.
    `persons` is `None` for a `*` room."""

    platform: str
    ids: frozenset[str]
    unit: str
    persons: frozenset[str] | None
    location: bool = False
    #: The operator's `policy.toml` label, or `""`: what a read tool calls
    #: the room to her (never an id, R-18).
    label: str = ""

    def is_room(self, conversation_id: str | None) -> bool:
        return conversation_id is not None and conversation_id in self.ids


@dataclass(frozen=True, slots=True)
class LiveRoom:
    """The room's roster as read at render time (`Room.scope()`): who is in it
    now, how many accounts map to nobody, and whether it could be read."""

    person_ids: frozenset[str]
    unnamed: int = 0
    available: bool = True
    degraded: bool = False


@dataclass(frozen=True, slots=True)
class RenderRoom:
    """Room B, and what the predicates read about every other room.

    `room` is `None` for a room outside the policy: nothing derived is shown
    into it. `circles` are the location-sharing circles, each as its members'
    person ids (the `DisclosureGuard` roster). `live` is `None` when no live
    roster was read; a predicate that needs one (`presence`,
    `exclude_persons`) then denies. `mode` travels with it so a context
    carries one value, not two.
    """

    room: RoomFacts | None
    rooms: tuple[RoomFacts, ...] = ()
    circles: tuple[frozenset[str], ...] = ()
    live: LiveRoom | None = None
    mode: LineageMode = "off"

    def with_live(self, live: LiveRoom | None) -> RenderRoom:
        return RenderRoom(
            room=self.room, rooms=self.rooms, circles=self.circles, live=live,
            mode=self.mode,
        )

    def resolve(self, conversation_id: str | None) -> tuple[RoomFacts, ...]:
        """Every policy room known by `conversation_id` in B's unit. Ids are
        unique per platform, and a derived row carries no platform (a note's
        `established_in`), so the unit is what makes the lookup one room."""
        if conversation_id is None or self.room is None:
            return ()
        unit = self.room.unit
        return tuple(r for r in self.rooms if r.unit == unit and conversation_id in r.ids)


def note_revision_lineage(
    *, cited: Iterable[int], rooms: Mapping[int, str], origin: str, first: bool,
    evidence_room: str | None = None,
) -> Lineage:
    """A note revision's lineage from its body's citations (migration
    `0070_lineage`'s backfill rule, applied to every new revision by
    `nora.infra.sql.notes`).

    `cited` are the `messages.id`s the body cites; `rooms` maps each one still
    on record to the room it was said in. Every such room is `message:<room>`;
    the FIRST revision also rests on the room the note was raised in
    (`origin`, its `established_in`); a revision written from a known room
    (the tag path's `note_update`) rests on that room too. A cited id no longer
    on record, or a later revision citing nothing from no known room, is
    `unresolved` at `origin`: shown only there (70 §5).
    """
    ids = frozenset(cited)
    entries = [message(rooms[i]) for i in ids if i in rooms]
    if first:
        entries.append(message(origin))
    if evidence_room:
        entries.append(message(evidence_room))
    if ids - rooms.keys() or not entries:
        entries.append(unresolved(origin))
    return Lineage.of({ROW: entries})


def own_room_lineage(room: str, sections: Iterable[str]) -> Lineage:
    """A Standing or culture row's lineage: its own room, and `unresolved` at
    it for what its fold was shown and did not cite (the unit-wide `<notes>`
    index). Shown only in `room` (migration `0070_lineage`)."""
    return Lineage.of({s: (message(room), unresolved(room)) for s in sections})
