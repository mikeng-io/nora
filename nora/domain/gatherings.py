"""Gathering/RSVP domain types and the `GatheringStore` port.

Mirrors `nora.domain.reminders`'s split (types + port in one module, no
separate `ports.py` entry — a domain module's own port lives with its
types, matching `ReminderStore`'s own precedent). An `Gathering` OWNS at most
one `Reminder` (`reminder_id`) when it has a `when` — Gathering never has a
recurrence concept of its own, so that reminder is always one-shot
(`Series` is never involved). Notifications are entirely the existing
`nora.domain.reminders` + `nora.app.fire` path; nothing here re-implements
scheduling.

Venue fields are resolved ONCE, at write time, through the same
`route_source`/`PlaceResolver` `nora.agent.tools`'s `place_info` tool
already uses (see `nora.app.gatherings.GatheringService`) — `venue_text` is what
was actually SAID; `venue_place_id`/`venue_name`/`venue_address`/
`venue_rating`/`venue_price_level`/`venue_maps_url` are the cached,
stable result of that one resolution. Raw `lat`/`lng` are deliberately
absent (DEC-24 — never in a model-facing schema, and `route()` never
accepts a coordinate as an argument anyway) and `open_now` is deliberately
absent (it goes stale by the hour; "is it open now" stays a fresh
on-demand check, never a cached column).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID

from nora.domain.lineage import CONFIDENCES, Lineage, LineageEntry, message, unresolved
from nora.domain.reminders import ensure_utc

#: design/cognition-loop/40-register.md §4: an event field's reasoning is one
#: of these three ("told", "deduced", "abduced" — never `computed`, `induced`
#: or `said`; `nora.domain.lineage.REASONING_MODES` is the full six other
#: tiers allow). `None` means "not stated", the same convention `0073_lineage`
#: set for note/standing/culture: no write verb states a mode until one
#: actually reasons about a field (design 30 §3 check 5 is what enforces
#: membership; step 2b only carries the column and the check, step 3 is the
#: first writer to set one on purpose).
EVENT_FIELD_MODES: frozenset[str] = frozenset({"told", "deduced", "abduced"})

#: 10-tiers.md §3's confidence vocabulary; identical to every other tier's,
#: reused rather than restated (`nora.domain.lineage.CONFIDENCES`).
EVENT_FIELD_CONFIDENCES: frozenset[str] = frozenset(CONFIDENCES)

#: The parts of an event a citation, a lineage section and a stated
#: reasoning each attach to (12-schemas.md §3: "what, start, end, place,
#: people, status"). One vocabulary for all three: `Gathering.lineage` is keyed
#: by it, `citation.part` holds it, `FieldMeta.field` names it.
EVENT_PARTS: tuple[str, ...] = ("what", "start", "end", "place", "people", "status")

#: What a citation names: a Record message (`cited` is its id, as text) or a
#: Rollup row (`cited` is `nora.domain.note_body.rollup_key`'s text).
CITED_MESSAGE = "message"
CITED_ROLLUP = "rollup"

#: design/cognition-loop/40-register.md §3: an event's lifecycle.
#: `PROPOSED`/`CONFIRMED`/`CANCELLED` predate this step (DEC-117); `STAGED`,
#: `HAPPENED`, `LAPSED` and `EVICTED` are 70-migration.md §2 row 2b's
#: addition. No further statuses "for now" (operator 2026-09-28).
class GatheringStatus(StrEnum):
    STAGED = "staged"
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    HAPPENED = "happened"
    CANCELLED = "cancelled"
    LAPSED = "lapsed"
    EVICTED = "evicted"


class RSVPResponse(StrEnum):
    YES = "yes"
    NO = "no"
    MAYBE = "maybe"


#: 40-register.md §3's lifecycle line: "Any status before `happened` may be
#: revised... No further statuses for happened/cancelled/lapsed/evicted."
#: Derived from `GatheringStatus` itself (every member except the three that
#: sit BEFORE `happened`) rather than hand-listed a second time, so a new
#: status added to the enum lands in exactly one bucket by construction
#: instead of silently falling into neither set.
_PRE_HAPPENED_STATUSES: frozenset[GatheringStatus] = frozenset({
    GatheringStatus.STAGED, GatheringStatus.PROPOSED, GatheringStatus.CONFIRMED,
})
#: How long a reminder whose event is marked unsupported waits past its
#: `due_at` for the mark to be cleared before it expires
#: (`nora.app.gathering_screen`): a gathering's reminder said late is still
#: useful for a little while and misleading after it.
HOLD_GRACE = timedelta(hours=2)

#: Why the fire-time screen held a gathering's reminder
#: (`nora_gathering_reminders_held_total{reason}`).
HELD_UNSUPPORTED = "unsupported"
HELD_READ_ERROR = "read_error"
HELD_REASONS: tuple[str, ...] = (HELD_UNSUPPORTED, HELD_READ_ERROR)

#: The statuses an event may still be revised out of (every one before
#: `happened`): what a Cognition session may move, and what it is shown.
OPEN_STATUSES: frozenset[GatheringStatus] = _PRE_HAPPENED_STATUSES
TERMINAL_STATUSES: frozenset[GatheringStatus] = frozenset(
    s for s in GatheringStatus if s not in _PRE_HAPPENED_STATUSES
)


class DuplicateGatheringError(Exception):
    """Raised by `GatheringStore.create` on a primary-key collision — mirrors
    `DuplicateReminderError`'s shape so callers handle both the same way."""

    def __init__(self, event_id: UUID) -> None:
        super().__init__(f"event {event_id} already exists")
        self.event_id = event_id


class InvalidEventFieldModeError(Exception):
    """Raised when a field's reasoning or confidence is not one the tier
    allows (design/cognition-loop/30-cognition.md §3 check 5: "the mode is
    one the tier allows"). `field` names which of `field_meta`'s keys failed;
    `value` is what was rejected."""

    def __init__(self, field_name: str, value: str) -> None:
        super().__init__(f"{field_name}: {value!r} is not an event field mode")
        self.field = field_name
        self.value = value


class StaleEventRevisionError(Exception):
    """Raised on an `event_revise`/`event_set_status`-shaped write whose
    `base_revision_id` is not the event's current `head_revision_id`
    (10-tiers.md §3: "a stale one is refused `stale_base`, with the current
    row"). Carries the row so the caller can show it without a second read."""

    def __init__(self, event_id: UUID, current: Gathering) -> None:
        super().__init__(f"event {event_id}: base_revision_id is stale")
        self.event_id = event_id
        self.current = current


def validate_event_field_mode(mode: str | None) -> None:
    """`None` ("not stated") always passes; anything else must be one of
    `EVENT_FIELD_MODES`."""
    if mode is not None and mode not in EVENT_FIELD_MODES:
        raise InvalidEventFieldModeError("reasoning", mode)


def validate_event_field_confidence(confidence: str | None) -> None:
    if confidence is not None and confidence not in EVENT_FIELD_CONFIDENCES:
        raise InvalidEventFieldModeError("confidence", confidence)


@dataclass(frozen=True, slots=True)
class FieldMeta:
    """One field's own stated reasoning, confidence and `as_of` (10 §3: "per
    field on an event"; 40 §4: "every field of every revision carries its
    reasoning, its citations, a confidence and `as_of`"). Both checked against
    the event tier's vocabularies, so a field cannot carry a mode the tier does
    not allow. `None` is "not stated"."""

    field: str
    reasoning: str | None = None
    confidence: str | None = None
    as_of: int | None = None

    def __post_init__(self) -> None:
        if self.field not in EVENT_PARTS:
            raise InvalidEventFieldModeError("field", self.field)
        validate_event_field_mode(self.reasoning)
        validate_event_field_confidence(self.confidence)


def field_mode(event: Gathering, part: str) -> str | None:
    """How `part` of `event` was reached (`told`, `deduced`, `abduced`), or
    `None` when the field states none (a row from before `0086`, or a part no
    write stated). The field's own mode wins; the row-level `reasoning` is only
    what a write that states one for the whole row carried, so it is the
    fallback and never overrides a field that says otherwise."""
    for m in event.field_meta:
        if m.field == part:
            return m.reasoning
    return event.reasoning


def render_field(event: Gathering, part: str, value: str) -> str:
    """One field of an event as it may be said (40 §4, 10 §2 rule 4): a value
    that rests only on abduction goes out as a QUESTION, never as a stated fact,
    whatever its confidence, and a person's «yeah» to her own guess never
    upgrades it (the stored mode stays `abduced` until a write restates the
    field on evidence of its own). Every other mode is stated. The one place a
    reader asks how to voice an event's field, so the `<events>` block and any
    tool that names one cannot disagree about it."""
    if field_mode(event, part) == "abduced":
        return f"{part}: {value}? (a guess to check, not a fact)"
    return f"{part}: {value}"


def merge_field_meta(
    held: Sequence[FieldMeta], stated: Sequence[FieldMeta],
) -> tuple[FieldMeta, ...]:
    """`held` with every field `stated` restates replaced, in `EVENT_PARTS`
    order: a write that moves the place says how the place was reached and
    leaves what it says of the time alone."""
    merged = {m.field: m for m in held}
    merged.update({m.field: m for m in stated})
    return tuple(merged[f] for f in EVENT_PARTS if f in merged)


def field_meta_to_json(meta: Sequence[FieldMeta]) -> dict[str, dict[str, object]]:
    """The `field_meta` column's value: field name to what it states."""
    return {
        m.field: {"reasoning": m.reasoning, "confidence": m.confidence, "as_of": m.as_of}
        for m in meta
    }


def field_meta_from_json(raw: object) -> tuple[FieldMeta, ...]:
    """Tolerant of a column that predates `0086` or a bad entry: anything that
    is not a known field with allowed values reads as not stated (`None`
    states nothing), never as an exception that takes a read down."""
    if not isinstance(raw, Mapping):
        return ()
    out: list[FieldMeta] = []
    for name in EVENT_PARTS:
        entry = raw.get(name)
        if not isinstance(entry, Mapping):
            continue
        mode, level, at = entry.get("reasoning"), entry.get("confidence"), entry.get("as_of")
        try:
            out.append(FieldMeta(
                field=name,
                reasoning=mode if isinstance(mode, str) else None,
                confidence=level if isinstance(level, str) else None,
                as_of=at if isinstance(at, int) and not isinstance(at, bool) else None,
            ))
        except InvalidEventFieldModeError:
            out.append(FieldMeta(field=name))
    return tuple(out)


@dataclass(frozen=True, slots=True)
class EventCitation:
    """One edge a write leaves in `citation`: `ref` (a message id as text, or
    a Rollup key) supports `part` of the event."""

    part: str
    store: str
    ref: str

    def __post_init__(self) -> None:
        if self.part not in EVENT_PARTS:
            raise InvalidEventFieldModeError("part", self.part)
        if self.store not in (CITED_MESSAGE, CITED_ROLLUP) or not self.ref.strip():
            raise InvalidEventFieldModeError("citation", f"{self.store}:{self.ref}")


@dataclass(frozen=True, slots=True)
class EventEvidence:
    """What a write rests on, handed to `GatheringStore.create`/`update` so
    the store can leave the edges and compute the lineage (10 §3: "code, from
    the citation chain; never the model").

    `parts` are the fields this write states; their lineage is recomputed from
    `citations` and `room`, every other field keeps what it held. `room` is the
    room key (`platform:conversation_id`) the write was said in, for a write
    whose evidence is the turn itself rather than a cited row (the tag path:
    `note_revision_lineage`'s `evidence_room`). A write that passes none of
    this leaves lineage as the row carries it: unknown, which renders only in
    its origin room."""

    parts: frozenset[str]
    citations: tuple[EventCitation, ...] = ()
    room: str | None = None

    def __post_init__(self) -> None:
        for part in self.parts:
            if part not in EVENT_PARTS:
                raise InvalidEventFieldModeError("part", part)


def event_lineage(
    held: Lineage, evidence: EventEvidence, *, message_rooms: Mapping[int, str],
    rollup_lineages: Mapping[str, Lineage | None], origin: str | None,
) -> Lineage:
    """The lineage of an event after a write that rests on `evidence` (10 §3:
    the set of `(kind, subject, origin room)` its citations reach, per FIELD).

    Each of `evidence.parts` is REPLACED by what its own citations reach (the
    value a write states rests on the evidence it cites, not on what the value
    it replaced rested on); every other field keeps what `held` carries. A
    cited message is `message:<the room it was said in>` (`message_rooms` holds
    the ids still on record); a cited Rollup row brings the entries its own
    lineage holds (`rollup_lineages`); a citation that cannot be resolved (a
    message gone from the Record, a Rollup row no longer current, a row whose
    lineage is empty) is `unresolved` at `origin`: shown only there, never
    silently widened. `evidence.room` is the room a write was SAID in when the
    turn itself is the evidence (the tag path), and adds `message:<room>`. A
    part with nothing at all behind it is `unresolved` too, never empty: an
    empty section would read as «unknown», which is the same denial but says
    less about why."""
    sections: dict[str, list[LineageEntry]] = {n: list(e) for n, e in held.sections}
    for part in evidence.parts:
        entries: list[LineageEntry] = []
        for c in evidence.citations:
            if c.part != part:
                continue
            if c.store == CITED_MESSAGE:
                room = message_rooms.get(int(c.ref)) if c.ref.isdigit() else None
                entries.append(message(room) if room else unresolved(origin))
                continue
            row = rollup_lineages.get(c.ref)
            reached = [] if row is None else [e for _, es in row.sections for e in es]
            entries.extend(reached or [unresolved(origin)])
        if evidence.room:
            entries.append(message(evidence.room))
        sections[part] = entries or [unresolved(origin)]
    return Lineage.of(sections)


@dataclass(frozen=True, slots=True)
class Gathering:
    id: UUID
    conversation_id: str
    title: str
    created_by: str  # person_id
    when: datetime | None
    venue_text: str | None
    venue_place_id: str | None
    venue_name: str | None
    venue_address: str | None
    venue_rating: float | None
    venue_price_level: str
    venue_maps_url: str | None
    venue_resolved_at: datetime | None
    status: GatheringStatus
    reminder_id: UUID | None
    created_at: datetime
    #: 70-migration.md §5 "Events.": unit-wide, origin room kept in
    #: `conversation_id`. Empty string until a caller resolves one (the tool
    #: layer's `_unit()`), matching `note`'s own "no unit means unavailable"
    #: convention rather than raising here.
    unit_id: str = ""
    #: The dated thing's end, alongside `when` as its start (12-schemas.md
    #: §3's `event` columns: "what, start, end, place, people, status").
    end: datetime | None = None
    #: Who is coming — deduced or abduced from co-location/RSVPs, or told.
    #: Distinct from `RSVP`: an RSVP is a person's OWN stated response: this
    #: is the event's field, cognition's to revise on evidence nobody stated
    #: (40-register.md §3).
    people: tuple[str, ...] = ()
    #: 10-tiers.md §3's per-derived-row columns. Row-level for now, the same
    #: simplification `0073_lineage` made for Standing and culture despite
    #: their sections ("until step 3/4's writers cite per section... render
    #: only in their own room" — DEC-236): `reasoning`/`confidence`/`as_of`
    #: stay NULL until a write verb states one for the row as a whole.
    #: `lineage` reuses `nora.domain.lineage.Lineage`, sectioned by FIELD name
    #: (`what`, `start`, `end`, `place`, `people`, `status`) — the one part
    #: of 10 §3 events get from day one, because `Lineage` already
    #: generalises to an arbitrary section name and per-field disclosure is
    #: what the design states outright ("per field on an event").
    reasoning: str | None = None
    confidence: str | None = None
    lineage: Lineage = field(default_factory=Lineage)
    as_of: int | None = None
    exclude_persons: tuple[str, ...] = ()
    #: The current `EventRevision.id` (10-tiers.md §3's compare-and-swap
    #: cache); `None` only before the first revision is written.
    head_revision_id: UUID | None = None
    #: Each field's own stated reasoning, confidence and `as_of`, in
    #: `EVENT_PARTS` order (migration `0086_event_citations`). The row-level
    #: `reasoning`/`confidence` above stay for a write that states one for the
    #: whole row.
    field_meta: tuple[FieldMeta, ...] = ()
    #: Epoch ms an event was marked withdrawn (10 §2 rule 7); `None` while it
    #: stands. READ only since 2026-10-03: the cascade that set it was removed, so only
    #: a row marked before then, or by an operator's SQL, carries it. A write never
    #: carries it (`compare=False`, and the store's write never sets it, so a revision
    #: read before a mark cannot overwrite it).
    unsupported_at: int | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        validate_event_field_mode(self.reasoning)
        validate_event_field_confidence(self.confidence)


@dataclass(frozen=True, slots=True)
class EventRevision:
    """One entry of an event's revision chain (`event_revision`,
    70-migration.md §5). Carries the FULL snapshot the way `note_revision`
    and `culture_revision` do — the head (`Gathering`) is a compare-and-swap
    cache over this chain (10-tiers.md §8), never the record of truth."""

    id: UUID
    event_id: UUID
    parent_revision_id: UUID | None
    status: GatheringStatus
    title: str
    when: datetime | None
    end: datetime | None
    venue_text: str | None
    venue_place_id: str | None
    venue_name: str | None
    venue_address: str | None
    venue_rating: float | None
    venue_price_level: str
    venue_maps_url: str | None
    people: tuple[str, ...]
    reasoning: str | None
    confidence: str | None
    lineage: Lineage
    as_of: int | None
    #: The `Plan`/`Open`/`Happened` Markdown body (12-schemas.md §3). `None`
    #: until a write verb actually composes one (step 3); this step carries
    #: the column, never requires it.
    body: str | None
    #: "tag" for the tag path, else the adaptor name that wrote it
    #: (30-cognition.md §3 check 10: "session id... stamped").
    written_by: str
    written_at: datetime
    field_meta: tuple[FieldMeta, ...] = ()


@dataclass(frozen=True, slots=True)
class RSVP:
    event_id: UUID
    person_id: str
    response: RSVPResponse
    responded_at: datetime


@dataclass(frozen=True, slots=True)
class VenueCandidate:
    """One `suggest_venue` candidate. `source` names WHY it was suggested —
    "mention_frequency" or "explicit_callback" — so a reply can say why,
    not just what."""

    venue_text: str
    place_id: str | None
    name: str | None
    address: str | None
    rating: float | None
    price_level: str
    maps_url: str | None
    source: str


@dataclass(frozen=True, slots=True)
class VenueSuggestions:
    """`status` is the R-27 three-state shape: `ok` / `nothing_found` /
    `unavailable` — an ERROR is never a PASS. `route_hints` is the reserved
    seam from spec §5.1: always `None` in this version; a later revision
    fills it via `saved_places()` + `route()` per attendee under the R-28
    guard those tools already enforce — the type does not change when it
    does."""

    status: str
    candidates: tuple[VenueCandidate, ...] = ()
    route_hints: None = None


class GatheringNotFoundError(Exception):
    """Raised by `GatheringService` (`nora.app.gatherings`) whenever an `event_id`
    does not resolve. Lives here, not on `GatheringService` itself, so
    `nora.agent` — which sits BELOW `nora.app` in the layered-architecture
    import-linter contract and may not import it — can still catch it: a
    caller several layers up mints the concrete `GatheringService`, but the
    exception TYPE has to be reachable from every layer that calls a method
    which can raise it."""

    def __init__(self, event_id: UUID) -> None:
        super().__init__(f"event {event_id} not found")
        self.event_id = event_id


class GatheringCancelledError(Exception):
    """Raised by `GatheringService` (`nora.app.gatherings`) when `update_event`/
    `rsvp` is asked to act on an event whose `status` is already in
    `TERMINAL_STATUSES` (`happened`, `cancelled`, `lapsed`, `evicted` —
    40-register.md §3: "Any status before `happened` may be revised... No
    further statuses" after it). Originally named for the one terminal
    status it covered (`CANCELLED`, Fix I2: a new `when` would otherwise
    re-arm the event's reminder for a gathering that was called off) — kept
    under this name rather than renamed when the check widened to the whole
    terminal set, so every existing catch site (`nora.agent.tools`, both
    layers' tests) keeps working; `status` says which terminal status it
    actually was. Lives here for the same layering reason as
    `GatheringNotFoundError`: the exception TYPE must be reachable from
    `nora.agent`, which sits below `nora.app` and may not import it."""

    def __init__(self, event_id: UUID, status: GatheringStatus | None = None) -> None:
        label = status.value if status is not None else "cancelled"
        super().__init__(f"event {event_id} is {label}")
        self.event_id = event_id
        self.status = status


class AmbiguousOpenGatheringsError(Exception):
    """More than one proposed event exists for a conversation — spec
    §3.1's fail-closed-on-ambiguity case. The caller (the `create_event`
    tool) is expected to catch this and ask which event is meant, never
    guess. Same layering reason as `GatheringNotFoundError` above for why this
    lives in `nora.domain` rather than alongside `GatheringService`."""

    def __init__(self, conversation_id: str, open_events: Sequence[Gathering]) -> None:
        super().__init__(
            f"{len(open_events)} open events for {conversation_id}"
        )
        self.conversation_id = conversation_id
        self.open_events = tuple(open_events)


def still_open_gatherings(
    events: Sequence[Gathering], *, now: datetime,
) -> list[Gathering]:
    """`GatheringService.create_event`'s I1 filter, extracted (review
    follow-up on #625's step 3a-i): a PROPOSED event still counts toward the
    ambiguity backstop only while it is STILL OPEN — `when is None` (no time
    settled yet) or `when >= now`. A dinner that already happened and was
    never explicitly cancelled must not refuse every future `create_event`
    call for this conversation forever.

    `events` is expected already filtered to `status=PROPOSED` (the caller's
    own read, `list_events(status=PROPOSED)` live or the equivalent shadow
    read) — this function does not read a store and does not filter by
    status itself, matching `has_duplicate_open_title`'s "prior is a
    parameter" shape next door in `nora.domain.notes`."""
    return [e for e in events if e.when is None or e.when >= now]


@runtime_checkable
class GatheringStore(Protocol):
    async def create(
        self, event: Gathering, *, evidence: EventEvidence | None = None,
    ) -> Gathering:
        """Mint the head and its first revision. `evidence`, when given, is
        what the write rests on: the store leaves one `citation` edge per
        `EventCitation` (citing store `event`, in the write's own transaction)
        and computes the lineage of `evidence.parts` from them. None leaves
        the row's own lineage."""

    async def get(self, event_id: UUID) -> Gathering | None:
        """The row as stored, withdrawn or not (`unsupported_at` says which):
        a WRITER needs the real row to revise it. A reader that renders it
        applies `unsupported_at` itself, or reads through a method below that
        does."""

    async def get_by_reminder(self, reminder_id: UUID) -> Gathering | None:
        """The event whose `reminder_id` is this reminder, as stored (withdrawn
        or not): what the fire-time screen reads
        (`nora.app.gathering_screen`)."""

    async def unsupported_reminder_ids(
        self, reminder_ids: Sequence[UUID],
    ) -> frozenset[UUID]:
        """Of `reminder_ids`, the ones a withdrawn event owns (`unsupported_at`
        set): reminders that are HELD and so not to be listed or announced
        (`nora.app.gathering_screen`). A reminder no event owns is not in it."""

    async def list_for_conversation(
        self, *, conversation_id: str, status: GatheringStatus | None = None,
        include_unsupported: bool = False,
    ) -> Sequence[Gathering]:
        """A room's events. Withdrawn ones (`unsupported_at`) are left out
        unless `include_unsupported`, which only a caller that re-writes every
        row (the units backfill) passes."""

    async def list_for_person(
        self, *, person_id: str, include_unsupported: bool = False,
    ) -> Sequence[Gathering]: ...

    async def update(
        self, event: Gathering, *, base_revision_id: UUID | None = None,
        evidence: EventEvidence | None = None,
    ) -> Gathering: ...

    async def add_rsvp(self, rsvp: RSVP) -> None: ...

    async def rsvps_for(self, event_id: UUID) -> Sequence[RSVP]: ...

    async def revisions_for(self, event_id: UUID) -> Sequence[EventRevision]: ...

    async def search(
        self, *, unit_id: str, place: str | None = None, person: str | None = None,
        start: datetime | None = None, end: datetime | None = None,
        room_ids: Sequence[str] = (),
    ) -> Sequence[Gathering]:
        """Unit-wide (`unit_id == unit_id`), PLUS (B3, 70-migration.md §8) any
        row with the migration's backfilled `unit_id=""` whose
        `conversation_id` is in `room_ids` — the unit's own rooms, resolved
        by the CALLER from `policy.toml` (units are not in the DB, so the
        migration could not fill this column for events that predate it).
        `room_ids=()` is the honest default: no legacy row is ever matched
        by accident when a caller has not resolved the unit's rooms. Never a
        cross-unit leak: a legacy row in another unit's room is never in
        `room_ids`, so it is never returned. A withdrawn row
        (`unsupported_at`) is never returned: this is a reader."""

    async def aclose(self) -> None: ...


def normalise_gathering(event: Gathering) -> Gathering:
    """`ensure_utc` over every one of an event's datetimes — the port's
    rule, in one place, exactly matching `normalise_reminder`'s shape."""
    from dataclasses import replace

    return replace(
        event,
        when=(
            None if event.when is None
            else ensure_utc(event.when, field="when")
        ),
        end=(
            None if event.end is None
            else ensure_utc(event.end, field="end")
        ),
        venue_resolved_at=(
            None if event.venue_resolved_at is None
            else ensure_utc(event.venue_resolved_at, field="venue_resolved_at")
        ),
        created_at=ensure_utc(event.created_at, field="created_at"),
    )
