from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from nora.agent.room import find_by_name
from nora.agent.toolkit.results import _record
from nora.agent.whereis import (
    Whereis,
    _dedupe,
    presence_audience,
)

log = logging.getLogger("nora.tools")


def _make_where_is(deps):
    content_checks = deps.content_checks
    geocoder = deps.geocoder
    location_trusted = deps.location_trusted
    metrics = deps.metrics
    presence_circles = deps.presence_circles
    presence_source = deps.presence_source
    refresher = deps.refresher
    room = deps.room

    async def where_is(person: str) -> dict:
        """Where somebody in this conversation is RIGHT NOW: a live reading, not a pattern.
        Call this when somebody asks where a person is.

        Give `person` as they were named. It forces a fresh reading first, so it
        may take a moment; only a member id goes out for that, never a coordinate.

        Returns `status`: ok, refused, not_found, ambiguous or unavailable, with
        a `note` on how to report it.

        Args:
            person: Who they asked about, exactly as they said it.
        """
        if presence_source is None or not presence_circles:
            # Presence not wired (offline/eval compositions, or `[presence]`
            # disabled / identity.toml carrying no circle). `unavailable`, never a
            # silent empty — a wiring gap must look like «I could not check», not
            _record(metrics, "where_is", "unavailable")
            return {
                "status": "unavailable",
                "note": "I can't check where people are right now. Say you have no "
                "way to see that, and do not guess.",
            }
        # One `Whereis` per circle, first `ok` wins. With one circle this is exactly
        # the seam's own behaviour. With several, a non-`refused` answer from a circle
        # the room IS a subset of outranks a `refused` from one it is not (the refusal
        results: list[dict] = []
        for circle in presence_circles:
            out = await Whereis(
                room=room,
                circle=circle,
                presence=presence_source,
                refresher=refresher,
                geocoder=geocoder,
                location_trusted=location_trusted,
                metrics=metrics,
                content_checks=content_checks,
            ).locate(
                person,
            )
            if str(out.get("status", "")) == "ok":
                _record(metrics, "where_is", "ok", age_seconds=out.get("age_seconds"))
                # `where_is` carries NO address (position + saved place +
                # lat/lng only), so the disclosed-live-address set (R-57) is
                # filled by `reverse_geocode`: the tool that resolves a
                return out
            results.append(out)
        answer = next(
            (r for r in results if r.get("status") != "refused"),
            results[0],
        )
        _record(metrics, "where_is", str(answer.get("status", "unavailable")))
        return answer

    return where_is


def _make__been_clock(deps):
    def _been_clock(minute_of_day: float | None) -> str | None:
        if minute_of_day is None:
            return None
        m = round(minute_of_day) % 1440
        return f"{m // 60:02d}:{m % 60:02d}"

    return _been_clock


def _make__been_iso(deps):
    _been_tz_name = deps._been_tz_name

    def _been_iso(ts_ms: int) -> str:
        """RFC 3339 with the substrate's timezone — the model-readable form of"""
        return datetime.fromtimestamp(ts_ms / 1000, tz=ZoneInfo(_been_tz_name)).isoformat(
            timespec="seconds"
        )

    return _been_iso


def _make__resolve_bound_place(deps):
    mobility = deps.mobility
    persons = deps.persons
    presence_circles = deps.presence_circles
    _place_words = deps._place_words
    _presence_audience = _make__presence_audience(deps)

    async def _resolve_bound_place(place_id: int) -> tuple[Any, str | None]:
        """A derived place for `route`, or `None` when this room may not be"""
        if mobility is None or not presence_circles:
            return None, None
        try:
            place = await mobility.place_by_id(place_id=place_id)
        except Exception:
            log.exception("route bound place read failed")
            return None, None
        if place is None:
            return None, None
        for circle in presence_circles:
            if not any(
                persons.subject_id_for(pid) == place.subject_id for pid in circle.person_ids
            ):
                continue
            _, refusal = await _presence_audience(
                circle,
                None,
                tool="route",
                anyone="where anyone's places are",
                people_phrase="people's places",
            )
            if refusal is None:
                from nora.infra.routes.places import Place

                name = _place_words(place.label, place.kind, place.saved_place_id is not None)
                return Place(
                    place_id=f"derived:{place.id}",
                    name=name,
                    address=name,
                    lat=place.lat,
                    lng=place.lng,
                ), place.label
        return None, None

    return _resolve_bound_place


def _make__presence_audience(deps):
    content_checks = deps.content_checks
    location_trusted = deps.location_trusted
    metrics = deps.metrics
    room = deps.room
    _been_everyone = deps._been_everyone

    async def _presence_audience(
        circle: Any,
        asked: str | None,
        *,
        tool: str,
        anyone: str,
        people_phrase: str,
    ) -> tuple[tuple[Any, ...], dict | None]:
        """Who a DERIVED-presence answer may be about, or why not: `(refs,"""
        view = await room.scope()
        if not view.available:
            return (), {
                "status": "unavailable",
                "note": "I can't work out who is in this group right now, so "
                f"I can't say {anyone}. Say so rather than guessing.",
            }
        # The decision is `presence_audience`'s, shared with a Cognition
        # rollup line's place release; the branches below word and count it.
        audience = presence_audience(
            view, circle, location_trusted=location_trusted, content_checks=content_checks
        )
        subset_ok = audience != "not_subset"
        unnamed_blocks = audience == "unnamed"
        if view.degraded:
            if metrics is not None:
                metrics.inc(
                    "nora_presence_disclosure_refusals_total",
                    reason="degraded",
                )
            log.warning(
                "%s could not verify the room (degraded): circle=%s location_trusted=%s",
                tool,
                circle.circle_id,
                location_trusted,
            )
            return (), {
                "status": "unavailable",
                "note": "I can't confirm who is in this group right now, so I "
                f"can't say {anyone}. Say you couldn't check, without "
                "guessing or blaming anyone's sharing settings.",
            }
        if unnamed_blocks or not subset_ok:
            # WHICH condition fired, so a refusal is diagnosable from the
            # metric instead of inferred — the same labels `locate` uses.
            reason = "unnamed" if unnamed_blocks else "not_subset"
            if metrics is not None:
                metrics.inc(
                    "nora_presence_disclosure_refusals_total",
                    reason=reason,
                )
            log.warning(
                "%s refused (%s): circle=%s unnamed=%d subset_ok=%s location_trusted=%s",
                tool,
                reason,
                circle.circle_id,
                view.unnamed,
                subset_ok,
                location_trusted,
            )
            return (), {
                "status": "refused",
                "note": f"I can't share {people_phrase} in this conversation: "
                "someone here is outside the location-sharing circle, "
                "or I can't confirm who is here. Say so in one clause "
                "and do not hint at the pattern.",
            }
        # A WAIVED guard is counted, not silent — the same DEC-79 rule
        # `Whereis.locate` runs one branch below its own guard.
        if content_checks and view.unnamed > 0 and location_trusted:
            if metrics is not None:
                metrics.inc(
                    "nora_presence_disclosure_waived_total",
                    reason="unnamed",
                )
            log.warning(
                "%s disclosed into a location-trusted room holding %d "
                "unmapped account(s) (DEC-79 waiver): circle=%s",
                tool,
                view.unnamed,
                circle.circle_id,
            )
        # R-47: who may be ASKED ABOUT resolves against the CIRCLE unioned with the
        # room — the audience gate has already been cleared above. No name (or 「大家」)
        # means the room's own people: the group asking about the group.
        if not asked or asked.casefold() in _been_everyone:
            refs = _dedupe(view.people)
            if not refs:
                return (), {
                    "status": "nothing_found",
                    "note": "I can't see who is in this conversation, so there "
                    "is no derived pattern to report. Say so, and do "
                    "not guess.",
                }
            return tuple(refs), None
        matches = _dedupe(find_by_name(circle.people, asked) + view.find(asked))
        if not matches:
            return (), {
                "status": "not_found",
                "note": "Nobody in this conversation goes by that. Say "
                "you're not sure who they mean, and do not guess.",
            }
        if len(matches) > 1:
            return (), {
                "status": "ambiguous",
                "could_be": [p.name for p in matches],
                "note": "More than one person here goes by that. Ask which one.",
            }
        return (matches[0],), None

    return _presence_audience
