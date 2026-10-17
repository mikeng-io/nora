"""`GatheringService` — the ONE use-case layer for Gathering business rules.

Gathering has two adapters (the agent tools and the web read routes); both call
these functions rather than each re-implementing what makes an event valid.

Reuses three existing pieces:
- the reminders domain, for an event's linked one-shot notification;
- the SAME `route_source` (`RouteClient`/`PlaceResolver`) the `place_info`
  tool already uses, for venue resolution — behind the same R-28 screen;
- Honcho's dialectic `chat()`, for `suggest_venue`'s per-person query.
"""

from __future__ import annotations

import logging
import urllib.parse
import uuid
from collections.abc import Callable, Collection, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from nora.agent.lineage import LineagePolicy, visible_sections
from nora.agent.security import looks_like_coordinate, scan_identifiers, screen_and_resolve_place
from nora.domain.gatherings import (
    CITED_MESSAGE,
    RSVP,
    TERMINAL_STATUSES,
    EventCitation,
    EventEvidence,
    FieldMeta,
    Gathering,
    GatheringStatus,
    GatheringStore,
    RSVPResponse,
    VenueCandidate,
    VenueSuggestions,
    merge_field_meta,
    still_open_gatherings,
)
from nora.domain.gatherings import (
    AmbiguousOpenGatheringsError as AmbiguousOpenGatheringsError,
)
from nora.domain.gatherings import (
    GatheringCancelledError as GatheringCancelledError,
)
from nora.domain.gatherings import (
    GatheringNotFoundError as GatheringNotFoundError,
)
from nora.domain.gatherings import (
    StaleEventRevisionError as StaleEventRevisionError,
)
from nora.domain.ports import PlaceArea
from nora.domain.reminders import (
    ensure_utc,
)
from nora.domain.rollup import room_key

#: The statuses the tag path's `event_set_status` tool may set: the lifecycle
#: minus `staged`/`proposed` (`event_propose`'s own outcomes) and `evicted`
#: (reserved to Cognition correcting its own misreads; the tag path never wrote
#: an event's first revision on Cognition's behalf).
TAG_SETTABLE_STATUSES: frozenset[GatheringStatus] = frozenset({
    GatheringStatus.CONFIRMED, GatheringStatus.CANCELLED,
    GatheringStatus.HAPPENED, GatheringStatus.LAPSED,
})

# The fixed-shape dialectic query: returns a short, directly-usable venue name
# (or a clear "none") rather than free prose needing a separate extraction
# step. Comparable across people and stable across calls because the wording
# never varies.
_VENUE_PREFERENCE_QUERY = (
    "In one short phrase, name ONE restaurant or venue this person has "
    "mentioned positively — food preferences and dietary constraints may "
    "help you pick. Reply with just the name, or the single word 'none' "
    "if you don't know one."
)

log = logging.getLogger("nora.app.gatherings")


class _RouteSource(Protocol):
    async def place_info(self, name: str) -> dict[str, Any]: ...


def screened_area(area: PlaceArea | None) -> PlaceArea | None:
    """`area` with each name that carries an identifier or a coordinate
    dropped (R-28, the return-path screen `_resolve_place` gives Google's
    name and address); None when nothing is left."""
    if area is None:
        return None

    def clean(name: str | None) -> str | None:
        if not name or scan_identifiers(name) is not None or looks_like_coordinate(name):
            return None
        return name

    return PlaceArea(district=clean(area.district), city=clean(area.city)) or None


class _PlaceAreas(Protocol):
    """A resolved venue's areas by its Google place id
    (`nora.infra.geocode.GoogleGeocoder.area_of_place`); None on a failure."""

    async def area_of_place(self, place_id: str) -> PlaceArea | None: ...


class _Honcho(Protocol):
    async def chat(
        self, *, peer_id: str, query: str, reasoning_level: str = "low",
        target: str | None = None, conversation_id: str | None = None,
    ) -> str | None: ...


def _tag_evidence(
    parts: set[str], platform: str, conversation_id: str,
    message_ids: Sequence[int] = (),
) -> EventEvidence | None:
    """What a tag-path write rests on: the turn itself, said in this room, and
    the message(s) that raised it (`Room.trigger_message_ids`, code's own
    knowledge, not something the model names: the tag path's event verbs take no
    `cited_ids`). Each field the write states gets one `citation` edge per
    message, so the withdrawal cascade reaches the event when that message is
    withdrawn. Its lineage is
    `message:<the room the message was said in>` per field, plus
    `message:<this room>` for the turn itself (`note_revision_lineage`'s
    `evidence_room` rule), so an event a tag turn wrote is not
    unknown-and-hidden under `[lineage] mode = on`. No ids (the fire path, a row
    outside the window) records no edge: the room-only lineage it always had.
    No fields, no evidence."""
    if not parts:
        return None
    return EventEvidence(
        parts=frozenset(parts),
        citations=tuple(
            EventCitation(part=part, store=CITED_MESSAGE, ref=str(mid))
            for part in sorted(parts) for mid in dict.fromkeys(message_ids)
        ),
        room=room_key(platform, conversation_id),
    )


def _stated(
    event: Gathering, reasoning: str | None, confidence: str | None,
    field_meta: tuple[FieldMeta, ...],
) -> Gathering:
    """`event` with what a write states of how it was reached: the fields it
    restates replace theirs (`merge_field_meta`), the rest keep what they
    carried. Nothing stated (the tag path) leaves the row as it was."""
    if not field_meta and reasoning is None and confidence is None:
        return event
    return replace(
        event, reasoning=reasoning or event.reasoning,
        confidence=confidence or event.confidence,
        field_meta=merge_field_meta(event.field_meta, field_meta),
    )


class GatheringService:
    def __init__(
        self,
        *,
        store: GatheringStore,
        route_source: _RouteSource | None,
        honcho: _Honcho | None,
        ai_peer: str,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        unit_room_ids: Callable[[str], Sequence[str]] | None = None,
        place_areas: _PlaceAreas | None = None,
    ) -> None:
        self._store = store
        self._route_source = route_source
        # A resolved venue's district and city, by its place id
        # (`GoogleGeocoder.area_of_place`); `None` (no Maps key) resolves
        # venues with no area, so the memory index gives them no `located_in`.
        self._place_areas = place_areas
        self._honcho = honcho
        self._ai_peer = ai_peer
        self._now = now
        # unit -> its room ids, from `policy.toml` (the composition root's
        # `_unit_rooms(allowlist)` mapping; units are not in the DB, so a
        # legacy event may carry no `unit_id`). `None` keeps `search_events`
        # to an exact `unit_id` match, surfacing no legacy row.
        self._unit_room_ids = unit_room_ids

    async def create_event(
        self, *, conversation_id: str, requester_person_id: str, title: str,
        when: datetime | None = None, venue_text: str | None = None,
        person_names: frozenset[str] | None = None, platform: str,
        unit_id: str = "", message_ids: Sequence[int] = (),
        status: GatheringStatus = GatheringStatus.PROPOSED,
        reasoning: str | None = None, confidence: str | None = None,
        field_meta: tuple[FieldMeta, ...] = (),
    ) -> Gathering:
        """`status`, `reasoning`, `confidence` and `field_meta` are what a
        Cognition session states with its write (how each field was
        reached; an abduced one is rendered as a question); the tag path leaves
        them at their defaults.

        The CALLER (the `create_event` tool) is expected to
        have already checked `list_events(status=PROPOSED)` and decided a
        new event is genuinely wanted — this method does not silently
        merge or refuse on its behalf. It DOES still raise
        `AmbiguousOpenGatheringsError` if two-or-more open events already
        exist, as a last-resort backstop, not the primary mechanism.

        Only a PROPOSED event that is STILL OPEN — `when is None` (no time
        settled yet) or `when >= now` (still ahead) — counts toward that
        backstop. Otherwise two past, never-cancelled dinners would refuse every
        future `create_event` for the conversation: "which one do you mean?"
        makes no sense for a long-past event, and there is no `confirm_event`
        transition to move a past event out of PROPOSED."""
        open_events = await self.list_events(
            conversation_id=conversation_id, status=GatheringStatus.PROPOSED,
        )
        now = self._now()
        still_open = still_open_gatherings(open_events, now=now)
        if len(still_open) >= 2:
            raise AmbiguousOpenGatheringsError(conversation_id, still_open)

        event = Gathering(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            title=title,
            created_by=requester_person_id,
            when=ensure_utc(when, field="when") if when is not None else None,
            venue_text=None,
            venue_place_id=None,
            venue_name=None,
            venue_address=None,
            venue_rating=None,
            venue_price_level="",
            venue_maps_url=None,
            venue_resolved_at=None,
            status=status,
            reminder_id=None,
            created_at=now,
            unit_id=unit_id,
            reasoning=reasoning,
            confidence=confidence,
            field_meta=field_meta,
        )
        if venue_text is not None:
            event = await self._resolve_venue(
                event, venue_text, now=now, person_names=person_names,
            )
        # `create` mints the first revision and returns the row WITH its
        # `head_revision_id` populated (the compare-and-swap value) — the
        # local `event` built above never has one.
        return await self._store.create(event, evidence=_tag_evidence(
            {"what", "status"}
            | ({"start"} if when is not None else set())
            | ({"place"} if venue_text is not None else set()),
            platform, conversation_id, message_ids,
        ))

    async def update_event(
        self, *, event_id: UUID, conversation_id: str,
        when: datetime | None = None, venue_text: str | None = None,
        person_names: frozenset[str] | None = None, platform: str,
        base_revision_id: UUID | None = None, message_ids: Sequence[int] = (),
        reasoning: str | None = None, confidence: str | None = None,
        field_meta: tuple[FieldMeta, ...] = (),
    ) -> Gathering:
        current = await self._require_in_conversation(event_id, conversation_id)
        if current.status in TERMINAL_STATUSES:
            # Only a status BEFORE `happened` may be
            # revised: `cancelled`, `happened`, `lapsed` and `evicted` are
            # never revised.
            raise GatheringCancelledError(event_id, current.status)
        updated = current
        if when is not None:
            updated = replace(updated, when=ensure_utc(when, field="when"))
        if venue_text is not None:
            updated = await self._resolve_venue(
                updated, venue_text, now=self._now(), person_names=person_names,
            )
        updated = _stated(updated, reasoning, confidence, field_meta)
        # A stale `base_revision_id` is refused `stale_base` with the current
        # row. It is optional (internal callers omit it) and is checked against
        # the row THIS CALL read, not re-read after `_resolve_venue`, like
        # every other refusal decided on the state `_require_in_conversation`
        # read.
        return await self._store.update(
            updated, base_revision_id=base_revision_id,
            evidence=_tag_evidence(
                ({"start"} if when is not None else set())
                | ({"place"} if venue_text is not None else set()),
                platform, conversation_id, message_ids,
            ),
        )

    async def set_event_status(
        self, *, event_id: UUID, conversation_id: str, status: GatheringStatus,
        base_revision_id: UUID | None = None, platform: str | None = None,
        message_ids: Sequence[int] = (),
        reasoning: str | None = None, confidence: str | None = None,
        field_meta: tuple[FieldMeta, ...] = (),
    ) -> Gathering:
        """The general lifecycle move `event_set_status` backs:
        `cancel_event` is `set_event_status(status=CANCELLED)` restated, kept
        as its own method for the callers (`nora.web.gatherings`) that use it."""
        current = await self._require_in_conversation(event_id, conversation_id)
        if current.status is GatheringStatus.CANCELLED and status is not GatheringStatus.CANCELLED:
            # The same refusal `update_event` gives a cancelled event: no
            # lifecycle move revives one that was called off.
            raise GatheringCancelledError(event_id)
        updated = _stated(replace(current, status=status), reasoning, confidence, field_meta)
        return await self._store.update(
            updated, base_revision_id=base_revision_id,
            evidence=None if platform is None else _tag_evidence(
                {"status"}, platform, conversation_id, message_ids,
            ),
        )

    async def rsvp(
        self, *, event_id: UUID, conversation_id: str, person_id: str,
        response: RSVPResponse,
    ) -> RSVP:
        current = await self._require_in_conversation(event_id, conversation_id)
        if current.status in TERMINAL_STATUSES:
            # The same set `update_event` refuses on:
            # recording an RSVP against an event that is over, called off,
            # lapsed, or evicted is not useful and reads as though it is
            # still on.
            raise GatheringCancelledError(event_id, current.status)
        row = RSVP(
            event_id=event_id, person_id=person_id, response=response,
            responded_at=self._now(),
        )
        await self._store.add_rsvp(row)
        return row

    async def cancel_event(self, *, event_id: UUID, conversation_id: str) -> Gathering:
        return await self.set_event_status(
            event_id=event_id, conversation_id=conversation_id,
            status=GatheringStatus.CANCELLED,
        )

    async def list_events(
        self, *, conversation_id: str, status: GatheringStatus | None = None,
    ) -> Sequence[Gathering]:
        return await self._store.list_for_conversation(
            conversation_id=conversation_id, status=status,
        )

    async def search_events(
        self, *, unit_id: str, conversation_id: str, place: str | None = None,
        person: str | None = None, start: datetime | None = None,
        end: datetime | None = None,
    ) -> Sequence[Gathering]:
        """`event_search`'s store side: unit-wide when
        `unit_id` names one (`list_events` is per room, filtered by status
        only); otherwise this room's own events, so a room with no unit
        resolved can still search its own events. A bare `event_search()` with
        none of `place`/`person`/`start`/`end` reads exactly as `list_events()`."""
        if not unit_id:
            return await self.list_events(conversation_id=conversation_id)
        room_ids: Sequence[str] = ()
        if self._unit_room_ids is not None:
            try:
                room_ids = self._unit_room_ids(unit_id)
            except Exception:
                # A resolver failure must not turn a unit-wide search
                # into a crash — it falls back to the exact `unit_id` match,
                # the same result as an unwired resolver (`None` above).
                log.exception("unit_room_ids resolution failed for unit %s", unit_id)
                room_ids = ()
        return await self._store.search(
            unit_id=unit_id, place=place, person=person, start=start, end=end,
            room_ids=room_ids,
        )

    async def get_event(self, *, event_id: UUID) -> Gathering | None:
        """A reader: an id that does not exist reads as absent."""
        return await self._store.get(event_id)

    async def list_events_for_person(
        self, *, person_id: str, conversation_ids: Collection[str] = (),
    ) -> Sequence[Gathering]:
        """The web app's ONLY read path (not conversation-scoped, unlike
        `list_events`, which the chat tools use): every event `person_id`
        created or RSVP'd to, across every conversation, plus every event held
        in one of `conversation_ids`, the rooms they are a member of. Without
        the rooms, an event Cognition records (created by `cognition:<unit>`,
        no RSVPs) is shown to nobody."""
        return await self._store.list_for_person(
            person_id=person_id, conversation_ids=conversation_ids,
        )

    async def get_event_for_person(
        self, *, event_id: UUID, person_id: str, conversation_ids: Collection[str] = (),
    ) -> Gathering | None:
        """The web app's per-event read: `None` (→ 404, never a distinct
        "forbidden") unless `person_id` created this event or has an RSVP
        on it — the same "an id from outside your scope reads as unknown"
        discipline `_require_in_conversation` already applies for the chat
        tools, applied here to event-level participation instead of
        conversation membership."""
        event = await self._store.get(event_id)
        if event is None:
            return None
        if event.created_by == person_id or event.conversation_id in conversation_ids:
            return event
        rsvps = await self._store.rsvps_for(event_id)
        if any(r.person_id == person_id for r in rsvps):
            return event
        return None

    async def rsvps_for(self, *, event_id: UUID) -> Sequence[RSVP]:
        """Read-only RSVP projection for a given event — the public
        passthrough `nora.web.gatherings` calls instead of reaching past the
        underscore into `self._store` directly."""
        return await self._store.rsvps_for(event_id)

    async def suggest_venue(
        self, *, event_id: UUID, conversation_id: str,
        roster_person_ids: Sequence[str],
        person_names: frozenset[str] | None = None,
    ) -> VenueSuggestions:
        await self._require_in_conversation(event_id, conversation_id)
        if self._honcho is None:
            return VenueSuggestions(status="unavailable")

        # One fixed-shape query per mapped person.
        mentions: dict[str, int] = {}
        for person_id in roster_person_ids:
            answer = await self._honcho.chat(
                peer_id=self._ai_peer, query=_VENUE_PREFERENCE_QUERY,
                target=person_id, conversation_id=conversation_id,
            )
            if not answer:
                continue
            name = answer.strip()
            if not name or name.lower() == "none":
                continue
            mentions[name] = mentions.get(name, 0) + 1

        if not mentions:
            return VenueSuggestions(status="nothing_found")

        ranked = sorted(mentions.items(), key=lambda kv: kv[1], reverse=True)
        candidates: list[VenueCandidate] = []
        for venue_text, count in ranked[:3]:
            resolved = await self._resolve_place(venue_text, person_names=person_names)
            candidates.append(
                VenueCandidate(
                    venue_text=venue_text,
                    place_id=resolved.get("place_id") if resolved else None,
                    name=resolved.get("name") if resolved else None,
                    address=resolved.get("address") if resolved else None,
                    rating=resolved.get("rating") if resolved else None,
                    price_level=(resolved or {}).get("price_level", ""),
                    maps_url=resolved.get("maps_url") if resolved else None,
                    source=(
                        "mention_frequency" if count > 1 else "explicit_callback"
                    ),
                )
            )
        return VenueSuggestions(status="ok", candidates=tuple(candidates))

    # ── internals ────────────────────────────────────────────────────────

    async def _require(self, event_id: UUID) -> Gathering:
        event = await self._store.get(event_id)
        if event is None:
            raise GatheringNotFoundError(event_id)
        return event

    async def _require_in_conversation(
        self, event_id: UUID, conversation_id: str,
    ) -> Gathering:
        """`_require`, plus the scope check `ReminderStore.cancel`
        (`nora.domain.reminders`) already documents the reasoning for: "a
        uuid carries no scope of its own — the STORE enforces the scope."
        `update_event`/`cancel_event`/`rsvp`/`suggest_venue` all
        accept a bare event id from the model with no prior proof it belongs
        to the room asking — an id from ANOTHER conversation refuses exactly
        like an unknown one (`GatheringNotFoundError`), never a distinct "wrong
        room" message that would confirm a stranger's event id is real."""
        event = await self._require(event_id)
        if event.conversation_id != conversation_id:
            raise GatheringNotFoundError(event_id)
        return event

    async def _resolve_venue(
        self, event: Gathering, venue_text: str, *, now: datetime,
        person_names: frozenset[str] | None = None,
    ) -> Gathering:
        resolved = await self._resolve_place(venue_text, person_names=person_names)
        if resolved is None:
            # A failed (or refused) re-resolution must not leave a PREVIOUS
            # successful resolution's name/address/rating/etc. paired with a
            # `venue_text` that did not actually resolve to it.
            return replace(
                event,
                venue_text=venue_text,
                venue_place_id=None,
                venue_name=None,
                venue_address=None,
                venue_rating=None,
                venue_price_level="",
                venue_maps_url=None,
                venue_resolved_at=None,
                venue_district=None,
                venue_city=None,
            )
        place_id = resolved.get("place_id")
        area = await self._area_of(str(place_id)) if place_id else None
        return replace(
            event,
            venue_text=venue_text,
            venue_place_id=place_id,
            venue_name=resolved.get("name"),
            venue_address=resolved.get("address"),
            venue_rating=resolved.get("rating"),
            venue_price_level=resolved.get("price_level", ""),
            venue_maps_url=resolved.get("maps_url"),
            venue_resolved_at=now,
            venue_district=area.district if area else None,
            venue_city=area.city if area else None,
        )

    async def _area_of(self, place_id: str) -> PlaceArea | None:
        """The venue's district and city, each screened on the way back the
        way `_resolve_place` screens Google's name and address (R-28): a
        name carrying an identifier or a coordinate is dropped, never
        stored. Never raises: a failure resolves no area."""
        if self._place_areas is None:
            return None
        try:
            area = await self._place_areas.area_of_place(place_id)
        except Exception:
            log.exception("venue area lookup failed; the venue keeps no area")
            return None
        return screened_area(area)

    async def _resolve_place(
        self, text: str, *, person_names: frozenset[str] | None = None,
    ) -> dict[str, Any] | None:
        """The FULL R-28 screen-call-screen sandwich, via the SAME
        `nora.agent.security.screen_and_resolve_place` `place_info`'s own
        tool closure calls, including the person-name check, the
        `try/except` around the call, and the return-path screen on Google's
        own `name`/`address` before they are persisted.

        `person_names` is optional because `GatheringService` is built once at
        process start and has no per-tag address book of its own — callers
        that have one (the tool closures in `nora.agent.tools`, which build
        it once per tag from the whole `persons` roster) pass it through;
        without it, this still gets the identifier/coordinate half."""
        result = await screen_and_resolve_place(
            text, route_source=self._route_source, person_names=person_names,
        )
        if result.status != "resolved":
            return None
        out = result.data
        if out is None:
            # Unreachable in practice — `status == "resolved"` always
            # carries `data` by construction — but degrades honestly (R-5)
            # rather than trusting that invariant with an `assert`.
            return None
        if out.get("status") != "ok":
            return None
        # The real `RouteClient.place_info` never returns `maps_url` — it is
        # synthesized only inside the `place_info` TOOL wrapper, a layer this
        # service never touches. Mint the identical link here: the resolved
        # name when Google gave one, else the original queried text.
        maps_name = str(out.get("name") or text)
        out = dict(out)
        out["maps_url"] = (
            "https://www.google.com/maps/search/?api=1&query="
            + urllib.parse.quote(maps_name)
        )
        return out


#: The event fields a room's view shows, the same four the Cognition session's
#: event index is checked over (`nora.app.cognition_grounding._EVENT_SHOWN_FIELDS`).
ROOM_EVENT_FIELDS = ("what", "start", "place", "status")


def room_event_shown(
    lineage: LineagePolicy, event: Gathering, viewer: str, platform: str, conversation_id: str,
) -> bool:
    """Whether a member of the room `(platform, conversation_id)` who neither
    made nor answered `event` may see it on the portal: never when the event
    is a surprise kept from them, and, with lineage enforced, only when every
    shown field passes for that room, as the chat path checks before a room's
    turn reads the event."""
    if viewer in event.exclude_persons:
        return False
    if lineage.mode != "on":
        return True
    target = lineage.render_room(platform=platform, conversation_id=conversation_id)
    return all(visible_sections(
        event.lineage, ROOM_EVENT_FIELDS, target,
        origin=room_key(platform, conversation_id), exclude_persons=event.exclude_persons,
    ).values())
