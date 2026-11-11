from __future__ import annotations

import logging
import urllib.parse
from types import SimpleNamespace
from typing import Any, Literal

from nora.agent.security import _names_a_person, scan_identifiers
from nora.agent.security import looks_like_coordinate as _looks_like_coordinate
from nora.agent.security import screen_and_resolve_place as _screen_and_resolve_place
from nora.agent.toolkit.results import _record
from nora.agent.whereis import (
    resolvable_places,
)
from nora.location.adapters.tools.shared import _MAPS_TRAVELMODE, _PLACE_INFO_UNAVAILABLE

log = logging.getLogger("nora.tools")


def _make_transit(deps):
    content_checks = deps.content_checks
    metrics = deps.metrics
    transit_source = deps.transit_source

    async def transit(route: str, near: str = "") -> dict:
        """When the next buses leave on a Hong Kong bus or feeder route. Call
        this for ANY question about a bus by number.

        It is live only: it answers about right now, never about a bus later
        today. Never state a bus time that did not come from here.

        Returns `status`: ok, nothing_found or unavailable (plus `near.status`
        when you passed `near`), with a `note` on how to report it.

        Args:
            route: The route number exactly as it was said.
            near: The stop or place name exactly as the person said it, for times
                at that stop. Leave it out unless they named one.
        """
        try:
            out = await transit_source.arrivals(route, near=near)
        except Exception:
            # `TransitClient.arrivals` is documented never to raise (it turns
            # every transport failure into `unavailable` itself). This is the belt
            # for those braces, and it must land on `unavailable` rather than on
            log.exception("transit failed")
            out = {
                "status": "unavailable",
                "note": "I could not check the buses just now. Say so, and do "
                "not say there are no buses or guess a time.",
            }
        # The same hole `_asked_about` and `calculate` exist for, in the third place a
        # MODEL-SUPPLIED string is handed straight back. `route` is already narrowed
        # to `[A-Z0-9]{1,7}` before it reaches a URL, so nothing that looks like a
        if scan_identifiers(str(out.get("route", "")), content_checks=content_checks) is not None:
            out.pop("route", None)
        # And the fourth: `near` is free text, echoed back as `near.asked` for the
        # same two-calls-one-turn disambiguation `_asked_about` buys, and screened
        # HERE because `nora.infra` sits below the scanner and cannot reach it. The
        near_info = out.get("near")
        if isinstance(near_info, dict):
            for key in ("asked", "stop"):
                if (
                    scan_identifiers(str(near_info.get(key, "")), content_checks=content_checks)
                    is not None
                ):
                    near_info.pop(key, None)
            if isinstance(near_info.get("could_be"), list):
                near_info["could_be"] = [
                    name
                    for name in near_info["could_be"]
                    if scan_identifiers(str(name), content_checks=content_checks) is None
                ]
        _record(
            metrics,
            "transit",
            out["status"],
            known_route=out.get("known_route"),
            directions=len(out.get("directions") or ()),
            **(
                {"near": near_info["status"]}
                if isinstance(near_info, dict) and "status" in near_info
                else {}
            ),
        )
        return out

    return transit


def _make__bind_place(deps):
    _bound_places = deps._bound_places

    def _bind_place(name: str, place_id: int, owner: str) -> str:
        """The words to show for this place, unique within the turn: the plain"""
        base = name.strip()
        first = owner.split()[0] if owner.strip() else ""
        candidate, n = base, 1
        while True:
            held = _bound_places.get(candidate.casefold())
            if held is None or held[0] == place_id:
                _bound_places[candidate.casefold()] = (place_id, owner)
                return candidate
            n += 1
            same_owner = _bound_places.get(base.casefold(), (0, ""))[1] == owner
            candidate = (
                f"{base} ({n})"
                if same_owner or not first
                else f"{base} ({first})"
                if n == 2
                else f"{base} ({first} {n - 1})"
            )

    return _bind_place


def _make_route(deps):
    async def route(
        origin: str,
        destination: str,
        mode: Literal["TRANSIT", "DRIVE", "WALK"] = "TRANSIT",
        arrive_by: str = "",
        by: Literal["bus", "mtr", "light_rail", "tram", "train"] | None = None,
        prefer: Literal["less_walking", "fewer_transfers"] | None = None,
        depart_after: str = "",
    ) -> dict:
        """How to get from one place to another, and how long it takes. Call
        this for ANY «how do I get from A to B».

        Give places as they were named. It looks each one up itself. For a
        PERSONAL place name (home, someone's home, work), call `saved_places`
        first and pass the saved name; never guess one from earlier messages.
        An `address` that `reverse_geocode` gave you for a `where_is` position
        MAY be the origin.

        Returns `status`: ok, no_route, unavailable or refused, with a `note` on
        how to report it.

        Args:
            origin: Where the trip STARTS, as a place name the way it was
                said, or exactly as routine_read or where_been named it.
            destination: Where the trip ENDS, as a place name.
            mode: TRANSIT (bus and rail, the default), DRIVE or WALK.
            arrive_by: Optional. When they need to ARRIVE, as a full timestamp
                with offset. Transit only; get it from the clock, never invent
                one.
            depart_after: Optional. When they want to LEAVE, as a full timestamp.
                Transit only. Give this or arrive_by, not both.
            by: Optional, and ONLY if the person named a way to travel. Transit
                only.
            prefer: Optional, only if they asked for less walking or fewer
                transfers. Transit only.
        """
        from nora.location.adapters.tools.presence import _make__resolve_bound_place

        c = SimpleNamespace(
            origin=origin,
            destination=destination,
            mode=mode,
            arrive_by=arrive_by,
            by=by,
            prefer=prefer,
            depart_after=depart_after,
            content_checks=deps.content_checks,
            location_trusted=deps.location_trusted,
            metrics=deps.metrics,
            presence_circles=deps.presence_circles,
            presence_source=deps.presence_source,
            room=deps.room,
            route_source=deps.route_source,
            _bound_places=deps._bound_places,
            _disclosed_live_addresses=deps._disclosed_live_addresses,
            _resolve_bound_place=_make__resolve_bound_place(deps),
            _route_person_names=deps._route_person_names,
        )
        k_, _, v_ = await _route__normalize(c)
        if k_:
            return v_
        k_, _, v_ = await _route__resolve_places(c)
        if k_:
            return v_
        return None  # type: ignore[return-value]

    return route


def _make__place_info_ok_note(deps):
    def _place_info_ok_note(*, has_rating: bool) -> str:
        """How to report a `place_info` `ok` (ADR 0015 §3), with the rating"""
        rating = (
            'Quote the rating as the one Google gives, with "about" in front of it.'
            if has_rating
            else "Google has no rating for this place yet: say you found it but it has "
            "no score, and never invent one."
        )
        return f"{rating} Include maps_url in your reply so they can tap through."

    return _place_info_ok_note


def _make_place_info(deps):
    content_checks = deps.content_checks
    metrics = deps.metrics
    route_source = deps.route_source
    _place_info_ok_note = _make__place_info_ok_note(deps)
    _route_person_names = deps._route_person_names
    link_carry = deps.link_carry

    async def place_info(name: str) -> dict:
        """How good, how pricey, and whether-open a place is, from Google.
        Call this for anything Google Maps knows.

        For a rating, use this: it returns Google's own number.

        Returns `status`: ok, ambiguous, not_found, unavailable or refused, with
        a `note` on how to report it.

        Args:
            name: The place, as it was said.
        """
        # The FULL R-28 screen-call-screen sandwich (outbound
        # identifier/coordinate/person screen, the actual call wrapped in try/except,
        # and a return-path screen on Google's own name/address) lives in
        result = await _screen_and_resolve_place(
            name,
            route_source=route_source,
            person_names=_route_person_names,
            content_checks=content_checks,
        )
        if result.status == "refused":
            _record(metrics, "place_info", "refused", reason=result.reason)
            if result.reason == "person":
                return {
                    "status": "refused",
                    "note": "I can only look up a PLACE, such as a shop or a "
                    "restaurant, not a person. Ask which place they mean.",
                }
            return {
                "status": "refused",
                "note": "That's not a place name I can look up. Give me the name of "
                "a shop, a restaurant, a building.",
            }
        if result.status == "unavailable":
            _record(metrics, "place_info", "unavailable")
            return {
                "status": "unavailable",
                "note": _PLACE_INFO_UNAVAILABLE,
            }
        out = result.data
        if out is None:
            # Unreachable in practice — `status == "resolved"` always carries
            # `data` by construction — but degrades honestly (R-5) instead of
            # trusting that invariant with an `assert` in production code.
            _record(metrics, "place_info", "unavailable")
            return {
                "status": "unavailable",
                "note": _PLACE_INFO_UNAVAILABLE,
            }
        if isinstance(out.get("could_be"), list):
            out["could_be"] = [
                c
                for c in out["could_be"]
                if scan_identifiers(str(c), content_checks=content_checks) is None
            ]
        # ADR 0015 §3: the client answers these three bare; how to report each
        # rides in the result, not in the description.
        if out.get("status") == "ambiguous":
            out["note"] = (
                "Several places match that name; could_be lists them. Ask which one they mean."
            )
        elif out.get("status") == "not_found":
            out["note"] = "Google knows no such place. Say so, and do not guess one."
        elif out.get("status") != "ok":
            out["note"] = _PLACE_INFO_UNAVAILABLE
        if out.get("status") == "ok":
            # A Google Maps link to the PLACE (search deep link, keyless), built
            # from the asked name — never a resolved coordinate (R-28, the same
            # rule route's maps_url follows). The full URL, never shortened (ADR 0023).
            maps_url = "https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote(name)
            out["maps_url"] = maps_url
            # DEC-89: offer it to the turn's carrier so that a reply which NAMES
            # this place carries the link whether or not the model remembered to
            # quote it. Two anchors for one url, because the words she is likely
            link_carry.offer(anchor=name, url=maps_url)
            link_carry.offer(anchor=str(out.get("name", "")), url=maps_url)
            out["note"] = _place_info_ok_note(has_rating=out.get("rating") is not None)
        _record(metrics, "place_info", out["status"], rating=out.get("rating"))
        return out

    return place_info


def _make_saved_places(deps):
    content_checks = deps.content_checks
    location_trusted = deps.location_trusted
    metrics = deps.metrics
    presence_circles = deps.presence_circles
    presence_source = deps.presence_source
    room = deps.room

    async def saved_places() -> dict:
        """The names this household has saved for places, for THIS conversation.
        Call this the moment someone routes to, or asks WHERE, a place by a
        PERSONAL name.

        A personal name is one Google doesn't know: home, someone's home, work,
        school. Asked where someone works or lives, use this, not a memory read.

        Returns `status`: ok or unavailable, with a `note` on how to report it.
        """
        if presence_source is None or not presence_circles:
            _record(metrics, "saved_places", "unavailable")
            return {
                "status": "unavailable",
                "note": "I can't see any saved places in this conversation. Say "
                "so and ask where they mean; don't invent one.",
            }
        places = await resolvable_places(
            room=room,
            circles=presence_circles,
            presence=presence_source,
            location_trusted=location_trusted,
            content_checks=content_checks,
        )
        # `None` is a FAILED READ, not an empty answer (R-27). Folding it into «no
        # names» would tell the model `ok` with an empty list while the presence store
        # is unreachable, and Nora would say 「你冇 save 過任何地方」 with `status="ok"` in the
        if places is None:
            _record(metrics, "saved_places", "unavailable")
            return {
                "status": "unavailable",
                "note": "I couldn't check the saved places just now. Say you "
                "couldn't look, not that there aren't any.",
            }
        # Names only — the coordinate is never model-facing (DEC-24/60). Empty in a
        # non-subset room (resolvable_places fell through), which is the R-35 shape:
        # the model simply has no household names to offer, and a guarded name stays
        names = sorted({p.name for p in places})
        # Addresses are carried for the names Life360 stores them on: a saved
        # place question («我返工嗰度個地址係咩») needs the street address
        # (SavedPlace.address). Never a coordinate; address only.
        addresses = {p.name: p.address for p in places if p.address}
        _record(metrics, "saved_places", "ok", count=len(names))
        # ADR 0015 §3: how to use the list rides with it, per field present.
        if names:
            note = (
                "Match their words to one of these names and pass that name "
                "to route or place_info, which uses the exact saved spot. If "
                "none fits, ask where they mean."
            )
        else:
            note = (
                "There are no saved names you may use here. Ask where they "
                "mean, or use a public place name."
            )
        note += " Never take a place from old messages."
        if addresses:
            note += (
                " addresses gives the street address for some names: answer "
                "an address question from it."
            )
        note += " Use these names only in this conversation."
        return {"status": "ok", "names": names, "addresses": addresses or None, "note": note}

    return saved_places


async def _route__normalize(c):
    c.by_mode, c.preference = (c.by or "", c.prefer or "")
    for _w1 in (c.origin, c.destination):
        c.place = _w1
        if not c.location_trusted and c.place.strip().casefold() in c._disclosed_live_addresses:
            _record(c.metrics, "route", "refused", reason="untrusted_position")
            return (
                True,
                None,
                {
                    "status": "refused",
                    "note": (
                        "That's somebody's live position, "
                        "not a place I can route from in this room. Do not retry: report the "
                        "position and "
                        "its age without a travel time."
                    ),
                },
            )
        if str(c.place).strip().casefold() in c._bound_places:
            continue
        if scan_identifiers(c.place, content_checks=c.content_checks) is not None:
            _record(c.metrics, "route", "refused", reason="identifier")
            return (
                True,
                None,
                {
                    "status": "refused",
                    "note": (
                        "That looks like an identifier, not "
                        "a place. Ask for a place name: a building, a stop, a mall."
                    ),
                },
            )
        if c.content_checks and _looks_like_coordinate(c.place):
            _record(c.metrics, "route", "refused", reason="coordinate")
            return (
                True,
                None,
                {
                    "status": "refused",
                    "note": (
                        "That's a raw coordinate, not a "
                        "place I can route from. Ask for the place by name: a building, a stop, a "
                        "mall."
                    ),
                },
            )
        if c.content_checks and _names_a_person(c.place, c._route_person_names):
            _record(c.metrics, "route", "refused", reason="person")
            return (
                True,
                None,
                {
                    "status": "refused",
                    "note": (
                        "I can't look up a route from or to "
                        "a person. Ask for the actual place (a building, a mall, a stop), not who "
                        "is there."
                    ),
                },
            )
    if c.route_source is None:
        _record(c.metrics, "route", "unavailable")
        return (
            True,
            None,
            {
                "status": "unavailable",
                "note": (
                    "I can't work out routes right now. "
                    "Say you couldn't check, not that there is no way to get there."
                ),
            },
        )
    c.ref_places: dict[str, Any] = {}
    return await _route__bind_places(c)


async def _route__bind_places(c):
    for _w2 in (c.origin, c.destination):
        c.place = _w2
        c.bound = c._bound_places.get(str(c.place).strip().casefold())
        if c.bound is None:
            continue
        c.resolved, c.raw_label = await c._resolve_bound_place(c.bound[0])
        if (
            c.resolved is not None
            and (not c.location_trusted)
            and c.raw_label
            and (c.raw_label.strip().casefold() in c._disclosed_live_addresses)
        ):
            _record(c.metrics, "route", "refused", reason="untrusted_position")
            return (
                True,
                None,
                {
                    "status": "refused",
                    "note": (
                        "That's somebody's live position, "
                        "not a place I can route from in this room. Do not retry: report the "
                        "position and "
                        "its age without a travel time."
                    ),
                },
            )
        if c.resolved is None:
            _record(c.metrics, "route", "refused", reason="bound_place")
            return (
                True,
                None,
                {
                    "status": "refused",
                    "note": (
                        "I can't route from that place in "
                        "this room. Ask for it by name instead, and do not guess where it is."
                    ),
                },
            )
        c.ref_places[str(c.place).strip().casefold()] = type(c.resolved)(
            place_id=c.resolved.place_id,
            name=str(c.place).strip(),
            address=str(c.place).strip(),
            lat=c.resolved.lat,
            lng=c.resolved.lng,
        )
    c.local_places = None
    return (False, None, None)


async def _route__resolve_places(c):
    if c.presence_source is not None and c.presence_circles:
        from nora.infra.routes.places import Place

        c.Place = Place
        c.saved = await resolvable_places(
            room=c.room,
            circles=c.presence_circles,
            presence=c.presence_source,
            location_trusted=c.location_trusted,
            content_checks=c.content_checks,
        )
        if c.saved:
            c.local_places = {
                p.name.strip().casefold(): c.Place(
                    place_id=f"saved:{p.place_id}",
                    name=p.name,
                    address=p.name,
                    lat=p.lat,
                    lng=p.lng,
                )
                for p in c.saved
            }
    c.ref_places = {k: v for k, v in c.ref_places.items() if k not in (c.local_places or {})}
    if c.ref_places:
        c.local_places = {**(c.local_places or {}), **c.ref_places}
    try:
        c.out = await c.route_source.route(
            c.origin,
            c.destination,
            mode=c.mode,
            arrive_by=c.arrive_by,
            by=c.by_mode,
            prefer=c.preference,
            depart_after=c.depart_after,
            local_places=c.local_places,
        )
    except Exception:
        log.exception("route failed")
        c.out = {
            "status": "unavailable",
            "origin": c.origin,
            "destination": c.destination,
            "note": (
                "I could not reach the routing service just now. Say so, and do not say there is "
                "no "
                "route or guess a time."
            ),
        }
    for _w1 in ("origin", "destination"):
        c.key = _w1
        c.asked_ref = c.ref_places.get(str(c.out.get(c.key, "")).strip().casefold())
        if c.asked_ref is not None:
            c.out[c.key] = c.asked_ref.name
        if scan_identifiers(str(c.out.get(c.key, "")), content_checks=c.content_checks) is not None:
            c.out.pop(c.key, None)
    for _w2 in ("origin_could_be", "destination_could_be"):
        c.key = _w2
        if isinstance(c.out.get(c.key), list):
            c.out[c.key] = [
                __cv
                for __cv in c.out[c.key]
                if scan_identifiers(str(__cv), content_checks=c.content_checks) is None
            ]
    if c.out.get("status") == "ok" and (not c.ref_places):
        c.out["maps_url"] = (
            f"https://www.google.com/maps/dir/?api=1&origin="
            f"{urllib.parse.quote(c.origin)}&destination="
            f"{urllib.parse.quote(c.destination)}&travelmode="
            f"{_MAPS_TRAVELMODE.get(str(c.out.get('mode', '')), 'transit')}"
        )
        c.out["note"] = " ".join(
            filter(
                None,
                (
                    str(c.out.get("note") or ""),
                    (
                        "maps_url links this "
                        "trip: paste it exactly as given when they want to open the map."
                    ),
                ),
            )
        )
    _record(
        c.metrics,
        "route",
        c.out["status"],
        mode=c.out.get("mode"),
        legs=len(c.out.get("legs") or ()),
    )
    return (True, None, c.out)
    return (False, None, None)
