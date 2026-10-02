"""`GatheringService` — the ONE use-case layer for Gathering business rules (spec
§1: reminders only ever had one adapter, so its rules live inline in a
tool closure; Gathering has two adapters from day one, so its rules live
here, and both the agent tool and the web read routes call these same
functions rather than each re-implementing what makes an event valid).

Reuses three things that already ship rather than rebuilding them:
- the reminders domain, for an event's linked one-shot notification;
- the SAME `route_source` (`RouteClient`/`PlaceResolver`) the `place_info`
  tool already uses, for venue resolution — behind the same R-28 screen;
- Honcho's dialectic `chat()`, for `suggest_venue`'s per-person query.
"""

from __future__ import annotations

import logging
import urllib.parse
import uuid
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from nora.agent.security import screen_and_resolve_place
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
from nora.domain.reminders import (
    Reminder,
    ReminderCaps,
    ReminderState,
    ReminderStore,
    ensure_utc,
)
from nora.domain.rollup import room_key

#: 40-register.md §3's lifecycle, minus the two the tag path's
#: `event_set_status` tool never offers: `staged`/`proposed` are
#: `event_propose`'s own outcomes, and `evicted` is reserved to "its own
#: misreads only" (30-cognition.md §3 check 12) — the tag path never wrote
#: this event's first revision on Cognition's behalf, so it is never the one
#: correcting a misread.
TAG_SETTABLE_STATUSES: frozenset[GatheringStatus] = frozenset({
    GatheringStatus.CONFIRMED, GatheringStatus.CANCELLED,
    GatheringStatus.HAPPENED, GatheringStatus.LAPSED,
})

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

log = logging.getLogger("nora.app.gatherings")


class _RouteSource(Protocol):
    async def place_info(self, name: str) -> dict[str, Any]: ...


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
    withdrawn (10 §2 rule 7; the review of PR #669). Its lineage is
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
        reminders: ReminderStore,
        reminder_caps: ReminderCaps,
        route_source: _RouteSource | None,
        honcho: _Honcho | None,
        ai_peer: str,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        unit_room_ids: Callable[[str], Sequence[str]] | None = None,
    ) -> None:
        self._store = store
        self._reminders = reminders
        self._caps = reminder_caps
        self._route_source = route_source
        self._honcho = honcho
        self._ai_peer = ai_peer
        self._now = now
        # B3(a): unit -> its room ids, from `policy.toml` (the composition
        # root's `_unit_rooms(allowlist)` mapping — units are not in the DB,
        # so the pre-migration backfill could not fill `unit_id` on an
        # existing event). `None` (every test, and any composition that
        # predates this fix) keeps `search_events` exactly as it was: an
        # exact `unit_id` match, no legacy row ever surfaced.
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
        Cognition session states with its write (40 §4: how each field was
        reached, an abduced one rendered as a question); the tag path leaves
        them at their defaults.

        Spec §3.1: the CALLER (the `create_event` tool) is expected to
        have already checked `list_events(status=PROPOSED)` and decided a
        new event is genuinely wanted — this method does not silently
        merge or refuse on its behalf. It DOES still raise
        `AmbiguousOpenGatheringsError` if two-or-more open events already
        exist, as a last-resort backstop, not the primary mechanism.

        Fix I1: only a PROPOSED event that is STILL OPEN — `when is None`
        (no time settled yet) or `when >= now` (still ahead) — counts
        toward that backstop. Without this, two dinners that already
        happened and were never explicitly cancelled would refuse every
        future `create_event` call for this conversation FOREVER — the
        "which one do you mean?" refusal makes no sense for an event that
        is long past, and there is no `confirm_event` transition to move
        a past event out of PROPOSED (a deliberate scope decision, not a
        gap this fix reopens)."""
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
        if when is not None:
            event = await self._link_reminder(
                event, requester_person_id=requester_person_id,
                conversation_id=conversation_id, platform=platform,
            )
        if venue_text is not None:
            event = await self._resolve_venue(
                event, venue_text, now=now, person_names=person_names,
            )
        # `create` mints the first revision and returns the row WITH its
        # `head_revision_id` populated (10-tiers.md §3's compare-and-swap
        # value) — the local `event` built above never had one.
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
            # 40-register.md §3: only a status BEFORE `happened` may be
            # revised. Fix I2 covered `CANCELLED` alone — a new `when` would
            # run `_link_reminder` and mint a fresh PENDING reminder for a
            # gathering that was called off; the same is true of `happened`
            # (re-arming a reminder for an event already over), `lapsed`,
            # and `evicted` — none of the four is ever revised.
            raise GatheringCancelledError(event_id, current.status)
        updated = current
        if when is not None:
            updated = replace(updated, when=ensure_utc(when, field="when"))
            updated = await self._link_reminder(
                updated, requester_person_id=updated.created_by,
                conversation_id=updated.conversation_id, platform=platform,
            )
        if venue_text is not None:
            updated = await self._resolve_venue(
                updated, venue_text, now=self._now(), person_names=person_names,
            )
        updated = _stated(updated, reasoning, confidence, field_meta)
        # 10-tiers.md §3: "a stale [base_revision_id] is refused `stale_base`,
        # with the current row" — `base_revision_id` (when the caller passes
        # one; internal callers that predate the event verbs do not) is
        # checked against the row THIS CALL read, not re-read after
        # `_link_reminder`/`_resolve_venue`, matching every other refusal
        # here being decided on the state `_require_in_conversation` read.
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
        """The general lifecycle move `event_set_status` backs (40-register.md
        §3): `cancel_event` is `set_event_status(status=CANCELLED)` restated,
        kept as its own method below for the one caller (`suggest_venue`'s
        sibling read paths, `nora.web.gatherings`) that predates this."""
        current = await self._require_in_conversation(event_id, conversation_id)
        if current.status is GatheringStatus.CANCELLED and status is not GatheringStatus.CANCELLED:
            # The same refusal `update_event` gives a cancelled event: no
            # lifecycle move revives one that was called off.
            raise GatheringCancelledError(event_id)
        if status is GatheringStatus.CANCELLED and current.reminder_id is not None:
            await self._reminders.cancel(
                current.reminder_id, conversation_id=current.conversation_id,
            )
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
            # 40-register.md §3, the same set `update_event` refuses on:
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
        """`event_search`'s store side (15-map.md §4: "events... **new**;
        `list_events` is per room, filtered by status only"). Unit-wide when
        `unit_id` names one; falls back to this room's own events
        (`list_events`'s old scope) when it does not — a room with no unit
        resolved is not left unable to search its own events at all, and a
        bare `event_search()` call with none of `place`/`person`/`start`/`end`
        given reads exactly as `list_events()` did (this step's explicit
        choice: the design leaves the exact routing between "this room" and
        "the unit" unstated beyond "unit-wide, filtered")."""
        if not unit_id:
            return await self.list_events(conversation_id=conversation_id)
        room_ids: Sequence[str] = ()
        if self._unit_room_ids is not None:
            try:
                room_ids = self._unit_room_ids(unit_id)
            except Exception:
                # B3(a): a resolver failure must not turn a unit-wide search
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

    async def list_events_for_person(self, *, person_id: str) -> Sequence[Gathering]:
        """The web app's ONLY read path (not conversation-scoped, unlike
        `list_events`, which the chat tools use): every event `person_id`
        created or RSVP'd to, across every conversation — deliberately not
        limited to one group, so a friend group split across Discord and
        Signal still sees the same event either way."""
        return await self._store.list_for_person(person_id=person_id)

    async def get_event_for_person(
        self, *, event_id: UUID, person_id: str,
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
        if event.created_by == person_id:
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

        # spec §5 step 2: one fixed-shape query per mapped person.
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
        Fix I5: `update_event`/`cancel_event`/`rsvp`/`suggest_venue` all
        accept a bare event id from the model with no prior proof it belongs
        to the room asking — an id from ANOTHER conversation refuses exactly
        like an unknown one (`GatheringNotFoundError`), never a distinct "wrong
        room" message that would confirm a stranger's event id is real."""
        event = await self._require(event_id)
        if event.conversation_id != conversation_id:
            raise GatheringNotFoundError(event_id)
        return event

    async def _link_reminder(
        self, event: Gathering, *, requester_person_id: str, conversation_id: str,
        platform: str,
    ) -> Gathering:
        """One-shot reminder only — Gathering never has a recurrence concept
        (Global Constraints). Respects the SAME `ReminderCaps` a manually
        requested reminder would, so an event cannot bypass the anti-runaway
        limits `remind` enforces (`min_lead_seconds`, `max_horizon_days`).

        `platform` is the REAL delivery platform of the conversation this
        gathering belongs to — the caller (the tool layer, which knows the
        room) passes `room.platform`. It replaces the historical
        `platform="event"` sentinel, which was never a wired adapter key, so
        a gathering reminder written with it could never be delivered
        (DEC-139; see design/event-abstraction/05-gathering-fold-in.md).
        """
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
            # The REAL delivery platform of the conversation — never the
            # "event" sentinel (DEC-139). A gathering-notify must resolve an
            # adapter exactly like a plain reminder does.
            platform=platform,
            conversation_id=conversation_id,
            conversation_type="group",
            # Legacy string kept for readers that still consume `text`;
            # the fire path reads payload["title"] for kind="gathering"
            # (design/event-abstraction/03-reminder-schema.md).
            text=f"Event: {event.title}",
            created_at=now,
            due_at=event.when,
            state=ReminderState.PENDING,
            fired_at=None,
            outcome=None,
            series=None,
            kind="gathering",
            payload={"title": event.title},
        )
        await self._reminders.create(row)
        return replace(event, reminder_id=reminder_id)

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

    async def _resolve_place(
        self, text: str, *, person_names: frozenset[str] | None = None,
    ) -> dict[str, Any] | None:
        """The FULL R-28 screen-call-screen sandwich, via the SAME
        `nora.agent.security.screen_and_resolve_place` `place_info`'s own
        tool closure calls (Fix I4) — previously this only ran the
        outbound identifier/coordinate half, with no person-name check, no
        `try/except` around the call, and no return-path screen on
        Google's own `name`/`address` before they were persisted.

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
