"""Gathering/RSVP domain types and the `GatheringStore` port.

Mirrors `nora.domain.reminders`'s split: types and port in one module, no
separate `ports.py` entry. A `Gathering` owns no reminder (DEC-303): whether,
when and what to remind about an event is a person's request or Nora's own
`remind`, which cites the event like any other row. `reminder_id` is always
cleared and nothing writes it.

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

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID

from nora.domain.lineage import CONFIDENCES, Lineage, LineageEntry, message, unresolved
from nora.domain.reminders import ensure_utc

#: An event field's reasoning is one of these three (never `computed`,
#: `induced` or `said`; `nora.domain.lineage.REASONING_MODES` is the full six
#: other tiers allow). `None` means "not stated", the same convention note,
#: standing and culture use: a write states a mode only when it actually
#: reasons about a field. Membership is enforced by `validate_event_field_mode`.
EVENT_FIELD_MODES: frozenset[str] = frozenset({"told", "deduced", "abduced"})

#: The confidence vocabulary, identical to every other tier's and reused
#: rather than restated (`nora.domain.lineage.CONFIDENCES`).
EVENT_FIELD_CONFIDENCES: frozenset[str] = frozenset(CONFIDENCES)

#: The parts of an event a citation, a lineage section and a stated
#: reasoning each attach to. One vocabulary for all three: `Gathering.lineage` is keyed
#: by it, `citation.part` holds it, `FieldMeta.field` names it.
EVENT_PARTS: tuple[str, ...] = ("what", "start", "end", "place", "people", "status")

#: What a citation names: a Record message (`cited` is its id, as text) or a
#: Rollup row (`cited` is `nora.domain.note_body.rollup_key`'s text).
CITED_MESSAGE = "message"
CITED_ROLLUP = "rollup"

#: An event's lifecycle (DEC-117). The closed set: no further statuses.
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


#: Any status before `happened` may be revised; `happened`, `cancelled`,
#: `lapsed` and `evicted` are terminal. Derived from `GatheringStatus` itself
#: (every member except the three that sit BEFORE `happened`) rather than
#: hand-listed a second time, so a new status added to the enum lands in
#: exactly one bucket by construction instead of silently falling into neither.
_PRE_HAPPENED_STATUSES: frozenset[GatheringStatus] = frozenset({
    GatheringStatus.STAGED, GatheringStatus.PROPOSED, GatheringStatus.CONFIRMED,
})
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
    allows. `field` names which of `field_meta`'s keys failed;
    `value` is what was rejected."""

    def __init__(self, field_name: str, value: str) -> None:
        super().__init__(f"{field_name}: {value!r} is not an event field mode")
        self.field = field_name
        self.value = value


class StaleEventRevisionError(Exception):
    """Raised on an `event_revise`/`event_set_status`-shaped write whose
    `base_revision_id` is not the event's current `head_revision_id`
    (a stale one is refused `stale_base`, with the current row). Carries the
    row so the caller can show it without a second read."""

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
    """One field's own stated reasoning, confidence and `as_of`, per field
    of an event. Both checked against
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
    `None` when the field states none (a row with no stated mode, or a part no
    write stated). The field's own mode wins; the row-level `reasoning` is only
    what a write that states one for the whole row carried, so it is the
    fallback and never overrides a field that says otherwise."""
    for m in event.field_meta:
        if m.field == part:
            return m.reasoning
    return event.reasoning


def render_field(event: Gathering, part: str, value: str) -> str:
    """One field of an event as it may be said: a value
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
    """Tolerant of an absent column or a bad entry: anything that
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
    the store can leave the edges and compute the lineage (computed by code
    from the citation chain, never by the model).

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
    """The lineage of an event after a write that rests on `evidence`: the
    set of `(kind, subject, origin room)` its citations reach, per FIELD.

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
    #: Unit-wide; the origin room is kept in `conversation_id`. Empty string
    #: until a caller resolves one (the tool layer's `_unit()`), matching
    #: `note`'s own "no unit means unavailable" convention rather than raising.
    unit_id: str = ""
    #: The OTHER half of the persisted owner (`0128_event_platform`): the
    #: platform the write was said on. Empty on a row that predates the column
    #: (or was written before durable platform ownership existed); such a row
    #: is quarantined by `event_in_scope`, never attributed from current policy.
    platform: str = ""
    #: The dated thing's end, alongside `when` as its start.
    end: datetime | None = None
    #: Who is coming — deduced or abduced from co-location/RSVPs, or told.
    #: Distinct from `RSVP`: an RSVP is a person's OWN stated response; this
    #: is the event's field, cognition's to revise on evidence nobody stated.
    people: tuple[str, ...] = ()
    #: Row-level derived-row columns, the same simplification Standing and
    #: culture make (DEC-236): `reasoning`/`confidence`/`as_of` stay NULL until
    #: a write verb states one for the row as a whole. `lineage` reuses
    #: `nora.domain.lineage.Lineage`, sectioned by FIELD name (`EVENT_PARTS`),
    #: so disclosure is per field.
    reasoning: str | None = None
    confidence: str | None = None
    lineage: Lineage = field(default_factory=Lineage)
    as_of: int | None = None
    exclude_persons: tuple[str, ...] = ()
    #: The current `EventRevision.id` (the compare-and-swap cache); `None` only
    #: before the first revision is written.
    head_revision_id: UUID | None = None
    #: Each field's own stated reasoning, confidence and `as_of`, in
    #: `EVENT_PARTS` order (migration `0086_event_citations`). The row-level
    #: `reasoning`/`confidence` above stay for a write that states one for the
    #: whole row.
    field_meta: tuple[FieldMeta, ...] = ()
    #: The areas the resolved venue lies in (`nora.domain.ports.PlaceArea`),
    #: resolved with it from Google's address components (migration
    #: `0118_place_area`); the memory index's `located_in` chain.
    venue_district: str | None = None
    venue_city: str | None = None

    def __post_init__(self) -> None:
        validate_event_field_mode(self.reasoning)
        validate_event_field_confidence(self.confidence)


@dataclass(frozen=True, slots=True)
class EventRevision:
    """One entry of an event's revision chain (`event_revision`).
    Carries the FULL snapshot the way `note_revision` and `culture_revision`
    do; the head (`Gathering`) is a compare-and-swap cache over this chain,
    never the record of truth."""

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
    #: The `Plan`/`Open`/`Happened` Markdown body. `None` until a write verb
    #: composes one; never required.
    body: str | None
    #: "tag" for the tag path, else the adaptor name that wrote it.
    written_by: str
    written_at: datetime
    field_meta: tuple[FieldMeta, ...] = ()
    #: The head's venue areas, snapshotted like its other venue fields.
    venue_district: str | None = None
    venue_city: str | None = None


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
    seam: always `None` today; it may later be filled via `saved_places()` +
    `route()` per attendee under the R-28 guard those tools already enforce,
    without changing the type."""

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
    `TERMINAL_STATUSES` (`happened`, `cancelled`, `lapsed`, `evicted`; only a
    status before `happened` may be revised). `status` says which terminal
    status it was; the name is kept for existing catch sites
    (`nora.agent.tools`). Lives here for the same layering reason as
    `GatheringNotFoundError`: the exception TYPE must be reachable from
    `nora.agent`, which sits below `nora.app` and may not import it."""

    def __init__(self, event_id: UUID, status: GatheringStatus | None = None) -> None:
        label = status.value if status is not None else "cancelled"
        super().__init__(f"event {event_id} is {label}")
        self.event_id = event_id
        self.status = status


class AmbiguousOpenGatheringsError(Exception):
    """More than one proposed event exists for a conversation: the
    fail-closed-on-ambiguity case. The caller (the `create_event`
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
    """`GatheringService.create_event`'s filter: a PROPOSED event still counts toward the
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


#: Conversation id -> every `(unit, platform)` pair `policy.toml` assigns it.
#: The PORTAL uses this to resolve a member room's trusted `(unit, platform)`;
#: it is never proof of who owns a persisted row. The row itself carries the
#: owner since `0128_event_platform`.
RoomClaims = Mapping[str, frozenset[tuple[str, str]]]


def event_in_scope(
    event: Gathering, *, unit_id: str, conversation_id: str,
    platform: str | None = None,
) -> bool:
    """Whether `event` may be read or mutated as `(unit_id, platform,
    conversation_id)`.

    The ROW is the ownership record: the `(unit_id, platform)` pair persisted
    with it names who wrote it. A row whose `platform` is empty has no durable
    platform identity — it predates `0128_event_platform` and `policy.toml`
    cannot establish what the policy was when it was written, because the room
    may since have moved. Such a row is QUARANTINED: preserved in the table,
    never shown or mutated through an inferred owner. The same applies to a row
    with an empty `unit_id` and a set platform: that is the deliberately
    unpartitioned development scope, and only a `unit_id=""` caller sees it.
    Both halves must match; one current policy assignment is not evidence.

    `platform=None` is the unit-wide reader (`event_search`, the agenda and
    the wake look-ahead): the unit is the read boundary (DEC-231), so any
    persisted platform in that unit is in scope. A platform-scoped read also
    requires the row's platform."""
    if event.conversation_id != conversation_id:
        return False
    if not event.platform:
        # No durable platform owner: unresolved historical row, quarantined.
        return False
    if event.unit_id != unit_id:
        return False
    if platform is None:
        return True
    return event.platform == platform


@runtime_checkable
class GatheringStore(Protocol):
    async def create(
        self, event: Gathering, *, evidence: EventEvidence | None = None,
    ) -> Gathering:
        """Mint the head and its first revision. `evidence`, when given, is
        what the write rests on: the store computes the lineage of
        `evidence.parts` from its `EventCitation`s. None leaves the row's own
        lineage."""

    async def get(self, event_id: UUID) -> Gathering | None:
        """The row as stored."""

    async def list_for_conversation(
        self, *, conversation_id: str, status: GatheringStatus | None = None,
        unit_id: str = "", platform: str | None = None,
    ) -> Sequence[Gathering]:
        """A room's events, scoped by the PERSISTED owner: the row's
        `conversation_id`, `unit_id` and (when named) `platform` must match.
        There is no `include_legacy` widening: a row with no stored platform
        is quarantined, never admitted through a current-policy inference."""

    async def list_for_person(
        self, *, person_id: str, conversation_ids: Collection[str] = (),
    ) -> Sequence[Gathering]:
        """Every event `person_id` created or RSVP'd to, and every event held in
        one of `conversation_ids` (the rooms they are a member of)."""

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
    ) -> Sequence[Gathering]:
        """Unit-wide (`unit_id == unit_id`). This is the unit read boundary
        (DEC-231): every persisted platform in that unit is in scope, and a row
        with no stored platform is quarantined (never matched by the old
        `room_ids` widening, which inferred historical ownership from current
        policy)."""

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
