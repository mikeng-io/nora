"""`EventService` — the ONE use-case layer for Event business rules (spec
§1: reminders only ever had one adapter, so its rules live inline in a
tool closure; Event has two adapters from day one, so its rules live
here, and both the agent tool and the web read routes call these same
functions rather than each re-implementing what makes an event valid).

Reuses three things that already ship rather than rebuilding them:
- the reminders domain, for an event's linked one-shot notification;
- the SAME `route_source` (`RouteClient`/`PlaceResolver`) the `place_info`
  tool already uses, for venue resolution — behind the same R-28 screen;
- Honcho's dialectic `chat()`, for `suggest_venue`'s per-person query.
"""

from __future__ import annotations

import urllib.parse
import uuid
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from nora.agent.security import looks_like_coordinate, scan_identifiers
from nora.domain.events import (
    RSVP,
    Event,
    EventStatus,
    EventStore,
    RSVPResponse,
    VenueCandidate,
    VenueSuggestions,
)
from nora.domain.events import (
    AmbiguousOpenEventsError as AmbiguousOpenEventsError,
)
from nora.domain.events import (
    EventNotFoundError as EventNotFoundError,
)
from nora.domain.reminders import (
    Reminder,
    ReminderCaps,
    ReminderState,
    ReminderStore,
    ensure_utc,
)

# The fixed-shape dialectic query, spec §5 step 2 — engineered to return a
# short, directly-usable venue name (or a clear "none") rather than free
# prose that would need a separate NL-extraction step. Comparable across
# people and stable across repeat calls because the wording never varies.
_VENUE_PREFERENCE_QUERY = (
    "In one short phrase, name ONE restaurant or venue this person has "
    "mentioned positively — food preferences and dietary constraints may "
    "help you pick. Reply with just the name, or the single word 'none' "
    "if you don't know one."
)


class _RouteSource(Protocol):
    async def place_info(self, name: str) -> dict[str, Any]: ...


class _Honcho(Protocol):
    async def chat(
        self, *, peer_id: str, query: str, reasoning_level: str = "low",
        target: str | None = None,
    ) -> str | None: ...


class EventService:
    def __init__(
        self,
        *,
        store: EventStore,
        reminders: ReminderStore,
        reminder_caps: ReminderCaps,
        route_source: _RouteSource | None,
        honcho: _Honcho | None,
        ai_peer: str,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._reminders = reminders
        self._caps = reminder_caps
        self._route_source = route_source
        self._honcho = honcho
        self._ai_peer = ai_peer
        self._now = now

    async def create_event(
        self, *, conversation_id: str, requester_person_id: str, title: str,
        when: datetime | None = None, venue_text: str | None = None,
    ) -> Event:
        """Spec §3.1: the CALLER (the `create_event` tool) is expected to
        have already checked `list_events(status=PROPOSED)` and decided a
        new event is genuinely wanted — this method does not silently
        merge or refuse on its behalf. It DOES still raise
        `AmbiguousOpenEventsError` if two-or-more open events already
        exist, as a last-resort backstop, not the primary mechanism."""
        open_events = await self.list_events(
            conversation_id=conversation_id, status=EventStatus.PROPOSED,
        )
        if len(open_events) >= 2:
            raise AmbiguousOpenEventsError(conversation_id, open_events)

        now = self._now()
        event = Event(
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
            status=EventStatus.PROPOSED,
            reminder_id=None,
            created_at=now,
        )
        if when is not None:
            event = await self._link_reminder(
                event, requester_person_id=requester_person_id,
                conversation_id=conversation_id,
            )
        if venue_text is not None:
            event = await self._resolve_venue(event, venue_text, now=now)
        await self._store.create(event)
        return event

    async def update_event(
        self, *, event_id: UUID, when: datetime | None = None,
        venue_text: str | None = None,
    ) -> Event:
        current = await self._require(event_id)
        updated = current
        if when is not None:
            updated = replace(updated, when=ensure_utc(when, field="when"))
            updated = await self._link_reminder(
                updated, requester_person_id=updated.created_by,
                conversation_id=updated.conversation_id,
            )
        if venue_text is not None:
            updated = await self._resolve_venue(updated, venue_text, now=self._now())
        await self._store.update(updated)
        return updated

    async def rsvp(
        self, *, event_id: UUID, person_id: str, response: RSVPResponse,
    ) -> RSVP:
        await self._require(event_id)  # 404s before writing an orphan RSVP
        row = RSVP(
            event_id=event_id, person_id=person_id, response=response,
            responded_at=self._now(),
        )
        await self._store.add_rsvp(row)
        return row

    async def cancel_event(self, *, event_id: UUID) -> Event:
        current = await self._require(event_id)
        if current.reminder_id is not None:
            await self._reminders.cancel(
                current.reminder_id, conversation_id=current.conversation_id,
            )
        updated = replace(current, status=EventStatus.CANCELLED)
        await self._store.update(updated)
        return updated

    async def list_events(
        self, *, conversation_id: str, status: EventStatus | None = None,
    ) -> Sequence[Event]:
        return await self._store.list_for_conversation(
            conversation_id=conversation_id, status=status,
        )

    async def get_event(self, *, event_id: UUID) -> Event | None:
        return await self._store.get(event_id)

    async def suggest_venue(
        self, *, event_id: UUID, roster_person_ids: Sequence[str],
    ) -> VenueSuggestions:
        await self._require(event_id)
        if self._honcho is None:
            return VenueSuggestions(status="unavailable")

        # spec §5 step 2: one fixed-shape query per mapped person.
        mentions: dict[str, int] = {}
        for person_id in roster_person_ids:
            answer = await self._honcho.chat(
                peer_id=self._ai_peer, query=_VENUE_PREFERENCE_QUERY,
                target=person_id,
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
            resolved = await self._resolve_place(venue_text)
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

    async def _require(self, event_id: UUID) -> Event:
        event = await self._store.get(event_id)
        if event is None:
            raise EventNotFoundError(event_id)
        return event

    async def _link_reminder(
        self, event: Event, *, requester_person_id: str, conversation_id: str,
    ) -> Event:
        """One-shot reminder only — Event never has a recurrence concept
        (Global Constraints). Respects the SAME `ReminderCaps` a manually
        requested reminder would, so an event cannot bypass the anti-runaway
        limits `remind` enforces (`min_lead_seconds`, `max_horizon_days`)."""
        if event.when is None:
            raise ValueError("_link_reminder requires event.when to be set")
        now = self._now()
        lead = (event.when - now).total_seconds()
        out_of_bounds = (
            lead < self._caps.min_lead_seconds
            or event.when - now > timedelta(days=self._caps.max_horizon_days)
        )
        # Whatever reminder this event previously pointed to must not survive
        # `_link_reminder` running again — otherwise it stays PENDING in the
        # store forever, fires at the wrong time, and `cancel_event` can never
        # reach it once the event row no longer references it. This holds on
        # BOTH the happy path (about to mint a replacement) and the
        # out-of-bounds path (about to clear reminder_id entirely).
        if event.reminder_id is not None:
            await self._reminders.cancel(
                event.reminder_id, conversation_id=conversation_id,
            )
        if out_of_bounds:
            # Out of bounds — the event itself is still created/updated;
            # it simply has no reminder. R-5: never silently pretend one
            # was set.
            return replace(event, reminder_id=None)

        reminder_id = uuid.uuid4()
        row = Reminder(
            id=reminder_id,
            requester_person_id=requester_person_id,
            platform="event",
            conversation_id=conversation_id,
            conversation_type="group",
            text=f"Event: {event.title}",
            created_at=now,
            due_at=event.when,
            state=ReminderState.PENDING,
            fired_at=None,
            outcome=None,
            series=None,
        )
        await self._reminders.create(row)
        return replace(event, reminder_id=reminder_id)

    async def _resolve_venue(
        self, event: Event, venue_text: str, *, now: datetime,
    ) -> Event:
        resolved = await self._resolve_place(venue_text)
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
            )
        return replace(
            event,
            venue_text=venue_text,
            venue_place_id=resolved.get("place_id"),
            venue_name=resolved.get("name"),
            venue_address=resolved.get("address"),
            venue_rating=resolved.get("rating"),
            venue_price_level=resolved.get("price_level", ""),
            venue_maps_url=resolved.get("maps_url"),
            venue_resolved_at=now,
        )

    async def _resolve_place(self, text: str) -> dict[str, Any] | None:
        """The R-28 screen, THEN the same resolver `place_info` calls —
        applied here because `EventService` reaches Google through a
        second path and must carry the identical guard (Task 6)."""
        if scan_identifiers(text) is not None or looks_like_coordinate(text):
            return None
        if self._route_source is None:
            return None
        out = await self._route_source.place_info(text)
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
