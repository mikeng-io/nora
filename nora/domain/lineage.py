"""Lineage: the `(kind, subject, origin room)` set a derived row's citations
reach down to.

This module is the DATA half: the entry and the per-section set, how they
serialise to the `lineage` JSONB column, and the closed vocabularies the
schema's CHECKs are frozen from. The per-room predicates that decide whether a
room may be shown a section are the other half and live in
`nora.agent.lineage`, beside the live `DisclosureGuard` the `presence`
predicate re-runs (DEC-222).

**The column's shape** is the one written on every Rollup row
(`nora.domain.rollup.lineage_json`), so there is one lineage format in the
store. An entry is the triple `[kind, subject, origin]`; a set is a JSON array
of them, sorted and de-duplicated. A row with lineage per ROW (a Register row,
a Rollup row) stores the array itself:

    [["message", null, "signal:<conversation_id>"]]

A row with lineage per SECTION stores an object, section name to array: a
Standing row has `body`, `working_set`, `hold_open` and `i_owe`; a culture row
(Character · room) has `body`, being prose with no sections (DEC-184).

**An entry.** `kind` names the predicate. `subject` is the person it is about
(`presence`, `face`, `social`, `play`) and null for `message`. `origin` is a
room, keyed `platform:conversation_id` (`room_key`): for `message` the room the
words were said in, for `face` the media's room, for `unresolved` the row's own
room. An optional fourth element holds the entries of the item a `perception`
or `face` entry reads (the lineage of the item it reads); Rollup rows write
none. A room reference without a known platform prefix is a bare
conversation id (a note's `established_in` carries no platform) and is matched
within the unit.

**Unknown is deny.** A section whose list is missing or empty, an
entry of a kind no predicate is written for, and a malformed entry all render
only in the row's origin room, and nowhere when it has none. `UNRESOLVED` is
the one such kind this code writes on purpose: a citation that could not be
resolved (a message pruned since, a note index a fold was shown and did not
record) is stated rather than dropped, so such a row gets origin-room-only
lineage.

Framework-free, per `nora/domain/`'s rule: stdlib and dataclasses only.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from nora.domain.rollup import room_key

__all__ = [
    "BODY",
    "CONFIDENCES",
    "FACE",
    "HOLD_OPEN",
    "I_OWE",
    "KINDS",
    "LINEAGE_MODES",
    "MESSAGE",
    "PERCEPTION",
    "PLATFORMS",
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
    "room_key",
    "split_room",
    "unresolved",
]

#: The lineage kinds a per-room predicate is written for. Closed: a kind not
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
#: unknown kind, so it is shown only in the row's origin room.
UNRESOLVED = "unresolved"

#: The section names today's schemas have (module docstring).
ROW = "row"
BODY = "body"
WORKING_SET = "working_set"
#: Room Standing's `hold_open` and `i_owe` sections
#: (`nora.domain.standing.Standing.hold_open`/`.i_owe`). `Where it stands` is
#: `BODY`; `Found, not yet given` is `WORKING_SET`.
HOLD_OPEN = "hold_open"
I_OWE = "i_owe"

#: The reasoning modes and confidence levels, the vocabularies the
#: `reasoning` and `confidence` columns are checked against. Migration
#: `0073_lineage` writes them out rather than importing them (a migration is a
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

    def to_json(self) -> list[Any]:
        out: list[Any] = [self.kind, self.subject, self.origin]
        if self.reads:
            out.append([r.to_json() for r in self.reads])
        return out

    @classmethod
    def from_json(cls, raw: Any, *, origin: str | None = None) -> LineageEntry:
        """Tolerant: anything that is not a well-formed triple (or its
        four-element form) becomes an `UNRESOLVED` entry at `origin`, never an
        exception and never a permissive value. A reader that raised here
        would take a whole block down over one bad row; one that skipped the
        entry would widen the row (fewer entries, fewer predicates to fail)."""
        if not isinstance(raw, list | tuple) or len(raw) not in (3, 4):
            return cls(kind=UNRESOLVED, origin=origin)
        kind, subject, entry_origin = raw[0], raw[1], raw[2]
        reads_raw = raw[3] if len(raw) == 4 else []
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
    equal and serialise byte-identically (a set)."""
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
        both rests on (a set)."""
        merged: dict[str, list[LineageEntry]] = {}
        for lineage in (self, other):
            for name, entries in lineage.sections:
                merged.setdefault(name, []).extend(entries)
        return Lineage.of(merged)

    def to_json(self) -> list[Any] | dict[str, Any]:
        """The array for a row-level set (the only section is `ROW`), the
        object of arrays otherwise (module docstring)."""
        if [name for name, _ in self.sections] == [ROW]:
            return [e.to_json() for e in self.sections[0][1]]
        return {name: [e.to_json() for e in entries] for name, entries in self.sections}

    @classmethod
    def from_json(cls, raw: Any, *, origin: str | None = None) -> Lineage:
        """The column's value as read back: an array is the row's one set
        (`ROW`), an object is per section. Anything else reads as `Lineage()`,
        every section unknown."""
        if isinstance(raw, list | tuple):
            if not raw:
                return cls()
            return cls.of({ROW: [LineageEntry.from_json(e, origin=origin) for e in raw]})
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


#: The platforms a room key may start with. A reference whose prefix is not one
#: of these is a bare conversation id.
PLATFORMS: frozenset[str] = frozenset({
    "signal", "discord", "whatsapp", "instagram", "telegram",
})


def split_room(ref: str) -> tuple[str | None, str]:
    """`platform:conversation_id` as its two parts, or `(None, ref)` for a
    bare conversation id."""
    platform, sep, conversation_id = ref.partition(":")
    if sep and platform in PLATFORMS and conversation_id:
        return platform, conversation_id
    return None, ref


def message(room: str) -> LineageEntry:
    """`message:<room>`: the row rests on something said in `room`, a room
    key (`room_key`): no subject, the room as origin."""
    return LineageEntry(kind=MESSAGE, subject=None, origin=room)


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
    #: The `culture` store's key for this room (`Conversation.primary_id`), or
    #: `""` for a room built without one. Never shown to a model (R-18).
    primary_id: str = ""

    def is_room(self, ref: str | None) -> bool:
        """Whether `ref`, a room key or a bare conversation id, names this
        room."""
        if ref is None:
            return False
        platform, conversation_id = split_room(ref)
        if platform is not None and platform != self.platform:
            return False
        return conversation_id in self.ids


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

    def resolve(self, ref: str | None) -> tuple[RoomFacts, ...]:
        """Every policy room `ref` (a room key, or a bare conversation id)
        names in B's unit. A bare id carries no platform (a note's
        `established_in`), so the unit is what makes that lookup one room."""
        if ref is None or self.room is None:
            return ()
        unit = self.room.unit
        return tuple(r for r in self.rooms if r.unit == unit and r.is_room(ref))


def note_revision_lineage(
    *, cited: Iterable[int], rooms: Mapping[int, str], origin: str,
    origin_key: str | None, first: bool, evidence_room: str | None = None,
    rollup_lineages: Mapping[str, Lineage | None] | None = None,
) -> Lineage:
    """A note revision's lineage from its body's citations (migration
    `0073_lineage`'s backfill rule, applied to every new revision by
    `nora.infra.sql.notes`).

    `cited` are the `messages.id`s the body cites; `rooms` maps each one still
    on record to the room key it was said in. Every such room is
    `message:<room>`. The FIRST revision also rests on the room the note was
    raised in: `origin` is its bare `established_in`, `origin_key` that room's
    key when the Record names its platform. A revision written from a known
    room (the tag path's `note_update`) rests on that room key too. A cited id
    no longer on record, a first revision whose room has no platform on
    record, or a later revision citing nothing from no known room, is
    `unresolved` at the origin: shown only there.

    `rollup_lineages` maps each Rollup key the body cites to the lineage of the
    current row it names (`None` for a key that names no current row): a cited
    row brings the entries its own lineage holds, so a note resting on a
    presence row is shown only where that row may be; a key that resolves to
    nothing is `unresolved` at the origin, never widened.
    """
    ids = frozenset(cited)
    here = origin_key or origin
    entries = [message(rooms[i]) for i in ids if i in rooms]
    for key in sorted(rollup_lineages or ()):
        row = (rollup_lineages or {}).get(key)
        reached = [] if row is None else [e for _, es in row.sections for e in es]
        entries.extend(reached or [unresolved(here)])
    if first:
        entries.append(message(origin_key) if origin_key else unresolved(here))
    if evidence_room:
        entries.append(message(evidence_room))
    if ids - rooms.keys() or not entries:
        entries.append(unresolved(here))
    return Lineage.of({ROW: entries})


def own_room_lineage(room: str, sections: Iterable[str], *, keyed: bool = True) -> Lineage:
    """A Standing or culture row's lineage: its own room, and `unresolved` at
    it for what its fold was shown and did not cite (the unit-wide `<notes>`
    index). Shown only in `room` (migration `0073_lineage`). `keyed=False` is
    a room with no platform on record: only the `unresolved` entry, since a
    `message` entry must name a room key."""
    entries = (message(room), unresolved(room)) if keyed else (unresolved(room),)
    return Lineage.of(dict.fromkeys(sections, entries))
