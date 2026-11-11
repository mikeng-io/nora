from __future__ import annotations

import logging
from typing import Literal
from uuid import UUID

from nora.agenda.adapters.tools.shared import (
    _EVENTS_OFF,
    _NO_SUCH_EVENT,
    _NOT_AN_EVENT_ID,
    _VENUE_REPORT,
)
from nora.agent.toolkit.clock import _parse_when
from nora.agent.toolkit.results import _record
from nora.domain.gatherings import (
    AmbiguousOpenGatheringsError,
    GatheringCancelledError,
    GatheringNotFoundError,
    GatheringStatus,
    RSVPResponse,
    StaleEventRevisionError,
)

log = logging.getLogger("nora.tools")


def _make_event_search(deps):
    events = deps.events
    metrics = deps.metrics
    room = deps.room
    _unit = deps._unit
    bound_conversation_id = deps.bound_conversation_id

    async def event_search(
        status: Literal["proposed", "confirmed", "cancelled"] | None = None,
        place: str = "",
        person: str = "",
    ) -> dict:
        """List the unit's gatherings. Call this before event_propose, so an
        open event gets revised instead of copied.

        Falls back to this room's own events when the unit cannot be
        resolved. Returns `status`: ok (each event's id, title, when, venue
        and status), refused or unavailable, with a `note` when it is not ok.

        Args:
            status: Only events in this state. Leave it out for all of them.
            place: Optional. Match the venue by name or the words used for it.
            person: Optional. Only events this person created or RSVP'd to.
        """
        if events is None:
            _record(metrics, "event_search", "unavailable")
            return dict(_EVENTS_OFF)
        parsed_status: GatheringStatus | None = None
        if status:
            try:
                parsed_status = GatheringStatus(status)
            except ValueError:
                _record(metrics, "event_search", "refused", reason="bad_status")
                return {
                    "status": "refused",
                    "note": "That's not a status I know. Use proposed, confirmed "
                    "or cancelled, or leave it out.",
                }
        try:
            if place or person:
                out = await events.search_events(
                    unit_id=_unit() or "",
                    conversation_id=bound_conversation_id,
                    place=place or None,
                    person=person or None,
                    platform=room.platform,
                )
                if parsed_status is not None:
                    out = [e for e in out if e.status is parsed_status]
            else:
                out = await events.list_events(
                    conversation_id=bound_conversation_id,
                    status=parsed_status,
                    unit_id=_unit() or "",
                    platform=room.platform,
                )
        except Exception:
            log.exception("event_search failed")
            _record(metrics, "event_search", "unavailable")
            return {
                "status": "unavailable",
                "note": "I couldn't check what's on. Say you couldn't check, and "
                "don't guess what's planned.",
            }
        _record(metrics, "event_search", "ok", count=len(out))
        return {
            "status": "ok",
            "events": [
                {
                    "id": str(e.id),
                    "title": e.title,
                    "when": e.when.isoformat() if e.when else None,
                    "venue": e.venue_name or e.venue_text,
                    "status": e.status.value,
                }
                for e in out
            ],
        }

    return event_search


def _make_event_propose(deps):
    events = deps.events
    metrics = deps.metrics
    room = deps.room
    _route_person_names = deps._route_person_names
    _unit = deps._unit
    bound_conversation_id = deps.bound_conversation_id

    async def event_propose(
        title: str,
        when: str = "",
        venue_text: str = "",
    ) -> dict:
        """Propose a NEW gathering in this conversation, returning its id. Call
        this when the room wants to arrange something that is not open yet.

        You MUST call event_search first: if an event is already open
        (proposed), revise it with event_revise instead of creating a second
        one.

        Returns `status`: ok, refused or unavailable, with a `note` when it is
        not ok.

        Args:
            title: What the gathering is, in their words.
            when: Optional. ISO 8601 with an offset, the first moment it is for.
                Work out relative time yourself, the same way remind does.
            venue_text: Optional. The place, the way it was said.
        """
        if events is None:
            _record(metrics, "event_propose", "unavailable")
            return dict(_EVENTS_OFF)
        tagger = room.tagger
        if tagger is None:
            _record(metrics, "event_propose", "refused", reason="no_tagger")
            return {
                "status": "refused",
                "note": "I couldn't tell who's asking, so nothing was created. Say so plainly.",
            }
        due_at = _parse_when(when) if when else None
        if when and due_at is None:
            _record(metrics, "event_propose", "refused", reason="bad_when")
            return {
                "status": "refused",
                "note": "I couldn't work out that time. Ask when they mean, or leave the time out.",
            }
        try:
            ev = await events.create_event(
                conversation_id=bound_conversation_id,
                requester_person_id=tagger.person_id,
                title=title,
                when=due_at,
                venue_text=venue_text or None,
                person_names=_route_person_names,
                platform=room.platform,
                unit_id=_unit() or "",
                message_ids=room.trigger_message_ids,
            )
        except AmbiguousOpenGatheringsError:
            _record(metrics, "event_propose", "refused", reason="ambiguous")
            return {
                "status": "refused",
                "note": "There's more than one open event here already. Ask which "
                "one they mean (event_search shows them), and don't guess.",
            }
        except Exception:
            log.exception("event_propose failed")
            _record(metrics, "event_propose", "unavailable")
            return {
                "status": "unavailable",
                "note": "I couldn't set that up. Say you're not sure it's on, "
                "rather than saying it is.",
            }
        _record(metrics, "event_propose", "ok")
        return {"status": "ok", "id": str(ev.id), "head_revision_id": str(ev.head_revision_id)}

    return event_propose


def _make_event_revise(deps):
    events = deps.events
    metrics = deps.metrics
    room = deps.room
    _route_person_names = deps._route_person_names
    _unit = deps._unit
    bound_conversation_id = deps.bound_conversation_id

    async def event_revise(
        event_id: str,
        when: str = "",
        venue_text: str = "",
        base_revision_id: str = "",
    ) -> dict:
        """Change the time or venue of an event ALREADY proposed here. Call this
        when an open event's details are settled or change.

        Returns `status`: ok (its `id`, `venue` and `head_revision_id`),
        refused or unavailable, with a `note` when it is not ok.

        Args:
            event_id: The event's id, from event_search.
            when: Optional. ISO 8601 with an offset.
            venue_text: Optional. The place, the way it was said.
            base_revision_id: Optional. The event's `head_revision_id` your
                last read of it showed. Leave it out to write regardless of
                what changed since.
        """
        if events is None:
            _record(metrics, "event_revise", "unavailable")
            return dict(_EVENTS_OFF)
        due_at = _parse_when(when) if when else None
        if when and due_at is None:
            _record(metrics, "event_revise", "refused", reason="bad_when")
            return {
                "status": "refused",
                "note": "I couldn't work out that time. Ask when they mean, or leave the time out.",
            }
        try:
            ev = await events.update_event(
                event_id=UUID(event_id),
                conversation_id=bound_conversation_id,
                when=due_at,
                venue_text=venue_text or None,
                person_names=_route_person_names,
                platform=room.platform,
                unit_id=_unit() or "",
                base_revision_id=UUID(base_revision_id) if base_revision_id else None,
                message_ids=room.trigger_message_ids,
            )
        except GatheringNotFoundError:
            _record(metrics, "event_revise", "refused", reason="not_found")
            return dict(_NO_SUCH_EVENT)
        except GatheringCancelledError:
            _record(metrics, "event_revise", "refused", reason="cancelled")
            return {
                "status": "refused",
                "note": "That event has already been called off, so there's "
                "nothing to update. Say so.",
            }
        except StaleEventRevisionError as exc:
            _record(metrics, "event_revise", "refused", reason="stale_base")
            return {
                "status": "refused",
                "note": "Someone else changed this event since you last read it "
                f"(it's now at revision {exc.current.head_revision_id}). "
                "Read it again before writing.",
            }
        except ValueError:
            _record(metrics, "event_revise", "refused", reason="bad_id")
            return dict(_NOT_AN_EVENT_ID)
        except Exception:
            log.exception("event_revise failed")
            _record(metrics, "event_revise", "unavailable")
            return {
                "status": "unavailable",
                "note": "I couldn't update it. Say you're not sure it changed, "
                "rather than saying it did.",
            }
        _record(metrics, "event_revise", "ok")
        return {
            "status": "ok",
            "id": str(ev.id),
            "venue": ev.venue_name or ev.venue_text,
            "head_revision_id": str(ev.head_revision_id),
        }

    return event_revise


def _make_event_rsvp(deps):
    events = deps.events
    metrics = deps.metrics
    room = deps.room
    _unit = deps._unit
    bound_conversation_id = deps.bound_conversation_id

    async def event_rsvp(
        event_id: str,
        response: Literal["yes", "no", "maybe"],
    ) -> dict:
        """Record whether the person talking is coming to an event already
        proposed here. Call this when somebody says they will, won't or might come.

        Returns `status`: ok, refused or unavailable, with a `note` when it is
        not ok.

        Args:
            event_id: The event's id, from event_search.
            response: Whether they are coming.
        """
        if events is None:
            _record(metrics, "event_rsvp", "unavailable")
            return dict(_EVENTS_OFF)
        tagger = room.tagger
        if tagger is None:
            _record(metrics, "event_rsvp", "refused", reason="no_tagger")
            return {
                "status": "refused",
                "note": "I couldn't tell who's asking, so nothing was recorded. Say so plainly.",
            }
        try:
            await events.rsvp(
                event_id=UUID(event_id),
                conversation_id=bound_conversation_id,
                person_id=tagger.person_id,
                response=RSVPResponse(response),
                platform=room.platform,
                unit_id=_unit() or "",
            )
        except GatheringNotFoundError:
            _record(metrics, "event_rsvp", "refused", reason="not_found")
            return dict(_NO_SUCH_EVENT)
        except GatheringCancelledError:
            _record(metrics, "event_rsvp", "refused", reason="cancelled")
            return {
                "status": "refused",
                "note": "That event has already been called off, so there's "
                "nothing to answer. Say so.",
            }
        except ValueError:
            _record(metrics, "event_rsvp", "refused", reason="bad_input")
            return {
                "status": "refused",
                "note": "That isn't an event id from event_search, or the answer "
                "isn't yes, no or maybe.",
            }
        except Exception:
            log.exception("event_rsvp failed")
            _record(metrics, "event_rsvp", "unavailable")
            return {
                "status": "unavailable",
                "note": "I couldn't record that. Say you're not sure it's saved, "
                "rather than saying it is.",
            }
        _record(metrics, "event_rsvp", "ok")
        return {"status": "ok"}

    return event_rsvp


def _make_event_set_status(deps):
    events = deps.events
    metrics = deps.metrics
    room = deps.room
    _unit = deps._unit
    bound_conversation_id = deps.bound_conversation_id

    async def event_set_status(
        event_id: str,
        status: Literal["confirmed", "cancelled", "happened", "lapsed"],
        base_revision_id: str = "",
    ) -> dict:
        """Move an event already proposed here to a new state. Call this when
        the room confirms it, says it happened, or says it is off.

        Returns `status`: ok (its `id` and `head_revision_id`), refused or
        unavailable, with a `note` when it is not ok.

        Args:
            event_id: The event's id, from event_search.
            status: The state to move it to.
            base_revision_id: Optional. The event's `head_revision_id` your
                last read of it showed. Leave it out to write regardless of
                what changed since.
        """
        if events is None:
            _record(metrics, "event_set_status", "unavailable")
            return dict(_EVENTS_OFF)
        try:
            parsed_status = GatheringStatus(status)
        except ValueError:
            _record(metrics, "event_set_status", "refused", reason="bad_status")
            return {
                "status": "refused",
                "note": "That's not a status I can set from here. Use confirmed, "
                "cancelled, happened or lapsed.",
            }
        try:
            ev = await events.set_event_status(
                event_id=UUID(event_id),
                conversation_id=bound_conversation_id,
                status=parsed_status,
                base_revision_id=UUID(base_revision_id) if base_revision_id else None,
                platform=room.platform,
                unit_id=_unit() or "",
                message_ids=room.trigger_message_ids,
            )
        except GatheringNotFoundError:
            _record(metrics, "event_set_status", "refused", reason="not_found")
            return dict(_NO_SUCH_EVENT)
        except GatheringCancelledError:
            _record(metrics, "event_set_status", "refused", reason="cancelled")
            return {
                "status": "refused",
                "note": "That event has already been called off. Say so.",
            }
        except StaleEventRevisionError as exc:
            _record(metrics, "event_set_status", "refused", reason="stale_base")
            return {
                "status": "refused",
                "note": "Someone else changed this event since you last read it "
                f"(it's now at revision {exc.current.head_revision_id}). "
                "Read it again before writing.",
            }
        except ValueError:
            _record(metrics, "event_set_status", "refused", reason="bad_id")
            return dict(_NOT_AN_EVENT_ID)
        except Exception:
            log.exception("event_set_status failed")
            _record(metrics, "event_set_status", "unavailable")
            return {
                "status": "unavailable",
                "note": "I couldn't change it. Say you're not sure it changed, "
                "rather than saying it did.",
            }
        _record(metrics, "event_set_status", "ok")
        return {
            "status": "ok",
            "id": str(ev.id),
            "head_revision_id": str(ev.head_revision_id),
        }

    return event_set_status


def _make_suggest_venue(deps):
    events = deps.events
    metrics = deps.metrics
    room = deps.room
    _route_person_names = deps._route_person_names
    _unit = deps._unit
    bound_conversation_id = deps.bound_conversation_id

    async def suggest_venue(event_id: str) -> dict:
        """Suggest venues for an event already proposed here, from places people
        in this conversation said they like. Call this when somebody wants a place.

        Returns `status`: ok (up to three `candidates`, each with name, address,
        rating, price_level, maps_url and why), nothing_found, refused or
        unavailable, with a `note` when it is not ok.

        Args:
            event_id: The event's id, from event_search.
        """
        if events is None:
            _record(metrics, "suggest_venue", "unavailable")
            return dict(_EVENTS_OFF)
        try:
            roster = await room.members()
        except Exception:
            # `Room.members` is documented never to raise (its own docstring:
            # "available=False if it could not be read"). This is the belt for
            # those braces, landing on `unavailable` rather than an unhandled
            log.exception("suggest_venue couldn't read the room")
            roster = None
        if roster is None or not roster.available:
            _record(metrics, "suggest_venue", "unavailable", reason="roster")
            return {
                "status": "unavailable",
                "note": "I couldn't read who's in this conversation right now. Say "
                "you couldn't check, and don't guess a venue.",
            }
        try:
            out = await events.suggest_venue(
                event_id=UUID(event_id),
                conversation_id=bound_conversation_id,
                roster_person_ids=[p.person_id for p in roster.people],
                person_names=_route_person_names,
                platform=room.platform,
                unit_id=_unit() or "",
            )
        except GatheringNotFoundError:
            _record(metrics, "suggest_venue", "refused", reason="not_found")
            return dict(_NO_SUCH_EVENT)
        except ValueError:
            _record(metrics, "suggest_venue", "refused", reason="bad_id")
            return dict(_NOT_AN_EVENT_ID)
        except Exception:
            log.exception("suggest_venue failed")
            _record(metrics, "suggest_venue", "unavailable")
            return {
                "status": "unavailable",
                "note": "I couldn't work out a suggestion. Say you couldn't "
                "check, and don't guess a venue.",
            }
        _record(metrics, "suggest_venue", out.status)
        result: dict = {
            "status": out.status,
            "candidates": [
                {
                    "venue_text": c.venue_text,
                    "name": c.name,
                    "address": c.address,
                    "rating": c.rating,
                    "price_level": c.price_level,
                    "maps_url": c.maps_url,
                    "why": c.source,
                }
                for c in out.candidates
            ],
        }
        # How to report a non-ok answer (ADR 0015 §3): the service returns
        # `nothing_found` (nobody has said anything usable) or `unavailable`
        # (no memory to ask) with no words of its own.
        if out.status in _VENUE_REPORT:
            result["note"] = _VENUE_REPORT[out.status]
        return result

    return suggest_venue
