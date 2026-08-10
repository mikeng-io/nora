"""`whereis(person)` and its R-21 disclosure seam (design/19 §5, design/14 §5.2).

`whereis` reads the LOCAL presence record for one person in this conversation,
carries the fix's AGE to the model, and degrades to `unavailable` (never
null-island) when there is no usable fix. Nora reads a local table; the collector
daemon absorbs Life360's fragility (§4), so `whereis` has no auth, no retry, no
challenge-page to handle. With a `LocationRefresher` wired (DEC-67) it first asks
the COLLECTOR to force a fresh reading before the read — Nora still never speaks
to Life360 herself, and a location REQUEST sends a member-id, not a coordinate,
so nothing sensitive leaves; the refresh is best-effort and falls back to the
stored fix.

**The disclosure guard is a NEW fail-closed seam, and this module does not
pretend the existing R-21 machinery covers it (both design reviews, HIGH).**
The room-scoped tools' R-21 is *resolve-within-scope*: a `RoomView` that is a
subset of the real room, so Nora cannot NAME someone who is not in the room.
Presence needs the INVERSE: refuse to DISCLOSE a circle member's position into a
room that contains someone OUTSIDE the circle. Nothing in `nora.agent.room`
compares a Life360 circle to a Signal group. So `DisclosureGuard` intersects the
conversation's people with the circle roster and REFUSES — not omits — on any
member outside it, and `check_disclosure_invariant` is the startup check that
alarms the day «group ⊄ circle» becomes true. Today circle == group ("Ridgeline")
so it holds by construction — VERIFIED, not assumed.

Two things this seam is careful NOT to weaken:
- **Refuse, don't omit.** A silent omission would read to the group as «I don't
  know where they are», which is R-5's shape; a refusal says why.
- **Outbound must stay hard-blocked** (design/14 §2.2): a stored position may
  seed an on-host route computation, but a coordinate must never leave in a tool
  argument. `whereis` returns a position for Nora's REPLY (in-group disclosure is
  fine, §5.3), which is a different exit than a Google query argument.

  That barrier EXISTS: `nora.agent.tools._looks_like_coordinate` refuses a raw
  coordinate in a `route` argument (council finding #4, 2026-08-07, R-28) — it
  landed BEFORE this tool was wired, exactly as the council required, so a
  `whereis` fallback coordinate cannot ride a route argument to Google.

Shipped 2026-08-07 as a testable SEAM (the DEC-48 inert shape), WIRED the same
day (DEC-59): `whereis` is in `NATIVE_TOOLS`, built by `build_tool_specs` over
this seam, and `ToolRegistry` holds the presence reader + circles the way it
holds the route client. The R-28 coordinate barrier in `nora.agent.tools`
(council #4) landed BEFORE the wiring, as required above.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import Any, Protocol

from ..domain.presence import PresenceFix, SavedPlace
from .room import Room

log = logging.getLogger("nora.agent.whereis")


class PresenceReader(Protocol):
    """The one read `whereis` needs from the presence store. Narrow on purpose,
    the same `_PrunableStore` shape `nora.app.runtime` uses: `whereis` programs
    against «the latest fix for this member», not the whole store."""

    async def latest_fix(
        self, *, circle_id: str, person_id: str
    ) -> PresenceFix | None: ...

    async def places_for(self, *, circle_id: str) -> list[SavedPlace]: ...

    async def window(
        self, *, circle_id: str, person_id: str, since_ts: int, until_ts: int
    ) -> list[PresenceFix]: ...


class LocationRefresher(Protocol):
    """Forces a LIVE on-demand reading for one person before `where_is` reads the
    store (DEC-67). The implementation (`nora.infra.presence_refresh`) calls the
    collector — which owns Life360 and the DB write — so `where_is` stays a pure
    reader. Best-effort and bounded: returns when a fresh fix is written OR the
    collector's timeout elapses; NEVER raises, so a failure leaves `where_is`
    reading the last stored fix."""

    async def refresh(self, *, person_id: str) -> bool: ...


@dataclass(frozen=True)
class Circle:
    """A Life360 circle as `whereis` needs it: its id and the set of `person_id`s
    in it. The `person_ids` are the disclosure roster — the set a conversation
    must be a SUBSET of. Built from `PresenceConfig` (the values of the
    circle→person_id map)."""

    circle_id: str
    person_ids: frozenset[str]


_EARTH_RADIUS_M = 6_371_000.0

# The movement read (design/19 §6, first slice — operator ask 2026-08-07):
# MEASURED facts off the stored fix history, never a prediction. A member is
# `moving` when their last two fixes inside this window are further apart than
# the noise floor; `towards` names a saved place only when the distance to it
# CLOSED by more than the floor across those fixes — «getting nearer to Home»
# is a measurement, «going home» is a guess, and only the first ships (R-5).
_MOVEMENT_WINDOW_MS = 15 * 60_000
_MOVEMENT_FLOOR_M = 150.0


def _distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(a))


def movement_view(
    fixes: list[PresenceFix], places: list[SavedPlace],
) -> dict[str, Any]:
    """`moving` / `towards` off the last two fixes in the window — pure,
    server-side, and empty when there is only one fix (one point has no
    direction; absence of the keys IS the honest «cannot tell»)."""
    if len(fixes) < 2:
        return {}
    prev, last = fixes[-2], fixes[-1]
    moved = _distance_m(prev.lat, prev.lng, last.lat, last.lng)
    out: dict[str, Any] = {"moving": moved > _MOVEMENT_FLOOR_M}
    if not out["moving"]:
        return out
    best_name, best_closed = None, _MOVEMENT_FLOOR_M
    for place in places:
        closed = (
            _distance_m(prev.lat, prev.lng, place.lat, place.lng)
            - _distance_m(last.lat, last.lng, place.lat, place.lng)
        )
        if closed > best_closed:
            best_name, best_closed = place.name, closed
    if best_name is not None:
        out["towards"] = best_name
    return out


def _within_place(lat: float, lng: float, place: SavedPlace) -> bool:
    """Haversine containment against the place's geofence (design/23 §4.1) —
    server-side geometry the model never performs; a place with no radius
    cannot contain."""
    if place.radius_m is None or place.radius_m <= 0:
        return False
    p1, p2 = math.radians(lat), math.radians(place.lat)
    dp = math.radians(place.lat - lat)
    dl = math.radians(place.lng - lng)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    d = 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(a))
    return d <= place.radius_m


async def resolvable_places(
    *, room: Room, circles: tuple[Circle, ...], presence: PresenceReader,
) -> tuple[SavedPlace, ...]:
    """The saved places THIS room may resolve by name (design/23 §4.1): only
    from circles the room is a verified subset of, and EMPTY on any doubt —
    degraded room, unmapped account, guard refusal, or a failed read. The
    caller falls through to the public lookup rather than refusing: a hard
    refusal would confirm a guarded name exists, and fall-through makes it
    indistinguishable from an unsaved one (R-21's own shape)."""
    try:
        view = await room.scope()
        if not view.available or view.degraded or view.unnamed > 0:
            return ()
        room_ids = frozenset(p.person_id for p in view.people)
        out: list[SavedPlace] = []
        for circle in circles:
            if DisclosureGuard(circle).permits(room_ids):
                out.extend(await presence.places_for(circle_id=circle.circle_id))
        return tuple(out)
    except Exception:
        log.exception("resolvable_places failed — falling through to public lookup")
        return ()


def circles_from_persons(persons: Any) -> tuple[Circle, ...]:
    """The `Circle`s Nora's own process wires `whereis` with, from identity.toml's
    `[[persons.<id>.life360]]` blocks — the same grouping the collector's
    `_circles_from_identity` does, with DIFFERENT failure semantics on purpose:
    the collector REFUSES to start on an unplaced block or an empty roster
    (enabled-but-collecting-nothing is R-5's shape), while here a block with no
    `circle_id` is simply skipped and an empty result is a valid answer —
    `whereis` degrades to `unavailable`, and Nora must not refuse to START over a
    tool that can honestly say «I can't check».

    Sorted by circle_id so the tuple is deterministic across restarts."""
    by_circle: dict[str, set[str]] = {}
    for membership in persons.life360:
        if membership.circle_id is None:
            continue
        by_circle.setdefault(membership.circle_id, set()).add(membership.person_id)
    return tuple(
        Circle(circle_id=circle_id, person_ids=frozenset(person_ids))
        for circle_id, person_ids in sorted(by_circle.items())
    )


class DisclosureGuard:
    """R-21 fail-closed (design/19 §5): permit disclosure only into a room whose
    people are a SUBSET of the circle.

    Fail-closed by construction:
    - an empty or unknown circle never permits (nothing to be a subset of);
    - a room that could not be read (unavailable/degraded) never permits — the
      subset cannot be VERIFIED, and an unverifiable invariant refuses.
    """

    def __init__(self, circle: Circle) -> None:
        self._circle = circle

    def permits(self, room_person_ids: frozenset[str]) -> bool:
        if not self._circle.person_ids:
            return False
        return room_person_ids <= self._circle.person_ids


def check_disclosure_invariant(
    *, circle: Circle, group_person_ids: frozenset[str], metrics: Any = None
) -> bool:
    """The startup invariant (design/19 §5, §7.7): is the Signal group a SUBSET
    of the circle? Publishes `nora_presence_circle_subset_ok` (1/0) and logs an
    ERROR the day it is not. Returns the boolean so a caller can refuse to wire
    `whereis` when it fails.

    This is the mechanical replacement for «the same rule the roster uses» —
    which the reviews called an overstatement, because nothing else in Nora
    compares a circle to a group.

    **UNWIRED, pending `whereis` activation (the DEC-48 inert shape).** It needs
    the Signal group roster, which the COLLECTOR process does not have — so it is
    called on Nora's side the day `whereis` is wired into `NATIVE_TOOLS`, not at
    collector startup. Until then it is a tested seam with no live call site (do
    not read a green `nora_presence_circle_subset_ok` as live coverage). The
    AUTHORITATIVE fail-closed check is the per-call guard in `Whereis.locate`,
    which also refuses on `view.unnamed > 0` — an unmapped account the coarse
    person_id subset here cannot see; this startup check is a belt-and-braces
    pre-flight over the KNOWN person_ids."""
    ok = bool(group_person_ids) and group_person_ids <= circle.person_ids
    if metrics is not None:
        metrics.gauge(
            "nora_presence_circle_subset_ok", 1.0 if ok else 0.0, circle=circle.circle_id
        )
    if not ok:
        outside = sorted(group_person_ids - circle.person_ids)
        log.error(
            "R-21 disclosure invariant FAILED: group is not a subset of circle %s; "
            "%d person(s) in the group are outside the circle — whereis must refuse "
            "until this is reconciled",
            circle.circle_id,
            len(outside),
        )
    return ok


class Whereis:
    """Read one person's latest recorded position, in scope, with its age.

    Composes the same `Room` the other room-scoped tools resolve within (name →
    `PersonRef`, membership), the `DisclosureGuard` (R-21), and a `PresenceReader`
    (the local record). Constructed fresh per tag like `Room` itself.
    """

    def __init__(
        self,
        *,
        room: Room,
        circle: Circle,
        presence: PresenceReader,
        refresher: LocationRefresher | None = None,
        location_trusted: bool = False,
        metrics: Any = None,
    ) -> None:
        self._room = room
        self._circle = circle
        self._presence = presence
        # Optional LIVE refresh (DEC-67): when wired, `where_is` asks the collector
        # to force a fresh reading before it reads the store. None → the old
        # read-only behaviour (the last stored fix), unchanged.
        self._refresher = refresher
        self._guard = DisclosureGuard(circle)
        # LOCATION-DISCLOSURE TRUST (DEC-74, widened by DEC-79): sourced from this
        # conversation's `policy.toml` `location` flag. It is a ROOM-LEVEL switch,
        # not a per-condition one: True relaxes BOTH the SUBSET check and the
        # `view.unnamed > 0` refusal below. `degraded` is NOT relaxed, and that
        # asymmetry is the point — an unnamed account is a roster Nora READ and
        # found an unknown in (a measurement the operator may overrule), whereas
        # `degraded` is a roster she could not read at all (an R-27 `unavailable`,
        # which is never a PASS no matter who trusts the room).
        self._location_trusted = location_trusted
        self._metrics = metrics

    async def locate(self, person: str, *, now_ms: int | None = None) -> dict[str, Any]:
        """Where `person` was last recorded, or why Nora cannot say. NEVER raises.

        Statuses, each a different answer:
          - `ok` — a position with its AGE (`age_seconds`), the address if the
            fix carried one, and the `wire_state` that fix was in.
          - `not_found` — nobody in this conversation goes by that name (says
            nothing about whether they exist elsewhere — the same R-21 shape the
            other room tools draw).
          - `ambiguous` — more than one person here matches; ask which.
          - `refused` — disclosure is refused because this conversation is not a
            subset of the circle (R-21 fail-closed) — never a silent omission.
          - `unavailable` — the room could not be read, or there is no usable
            fix. Never null-island, never a guessed position.
        """
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        view = await self._room.scope()
        if not view.available:
            return self._unavailable(
                "I can't work out who is in this group right now, so I can't say "
                "where anyone is. Say so rather than guessing."
            )
        # Disclosure is checked against the WHOLE room, before any name is
        # resolved: if anyone here is outside the circle, no member's position may
        # be disclosed into this conversation at all (§5). Three ways the room
        # fails the subset test, and ALL of them refuse — fail-closed on any
        # uncertainty:
        #   - a RESOLVED member whose person_id is not in the circle;
        #   - `view.unnamed > 0` — an account in the room resolving to NO
        #     person_id is by definition unverifiable against the circle, and it
        #     is the LIKELIEST real trigger (a stranger not in identity.toml joins
        #     the group, design/19 §5's own example). `view.people` is only the
        #     RESOLVABLE subset, so checking it alone would silently disclose past
        #     an unmapped outsider (both #37 reviews, 2026-08-07);
        #   - a DEGRADED view (tagger-only, roster unreadable) that cannot verify
        #     the subset at all.
        room_person_ids = frozenset(p.person_id for p in view.people)
        # DEC-79 widened DEC-74: `location = true` relaxes the SUBSET check AND
        # the unnamed refusal, because the operator's real rooms permanently hold
        # sealed-sender accounts (no phone number on the wire, never spoken, so
        # nothing to key `identity.toml` on) and a per-condition flag only asked
        # him the same question twice. `degraded` still refuses for everyone:
        # trust can overrule what a roster SAYS, never the absence of a roster.
        subset_ok = self._location_trusted or self._guard.permits(room_person_ids)
        unnamed_blocks = view.unnamed > 0 and not self._location_trusted
        if view.degraded or unnamed_blocks or not subset_ok:
            # WHICH of the three conditions fired — so a refusal is diagnosable
            # from the trace/metric instead of inferred (obs review 2026-08-08).
            # Precedence matches the disjunction: degraded first (can verify
            # nothing), then an unmapped account, then the subset itself.
            reason = (
                "degraded" if view.degraded
                else "unnamed" if unnamed_blocks
                else "not_subset"
            )
            if self._metrics is not None:
                self._metrics.inc(
                    "nora_presence_disclosure_refusals_total", reason=reason
                )
            log.warning(
                "whereis refused (%s): circle=%s degraded=%s unnamed=%d subset_ok=%s "
                "location_trusted=%s",
                reason, self._circle.circle_id, view.degraded, view.unnamed,
                subset_ok, self._location_trusted,
            )
            return {
                "status": "refused",
                "note": "I can't share where people are in this conversation — "
                        "someone here is outside the location-sharing circle, or I "
                        "can't confirm who is here. Say so; do not guess.",
            }
        matches = view.find(person)
        if not matches:
            return {
                "status": "not_found",
                "note": "Nobody in this conversation goes by that. Say you're not "
                        "sure who they mean — do not guess.",
            }
        if len(matches) > 1:
            return {
                "status": "ambiguous",
                "could_be": [p.name for p in matches],
                "note": "More than one person here goes by that. Ask which one.",
            }
        ref = matches[0]
        # LIVE refresh BEFORE the read (DEC-67, operator: "nora is asking WHERE IS,
        # not where they used to be"): ask the collector to ping this person's
        # phone and write a fresh fix, so the read below sees NOW, not the last
        # battery-saved report. Runs only after disclosure passed and the person
        # resolved — never for someone the room may not ask about. Best-effort: a
        # timeout or failure leaves the stored fix, which the read still returns.
        if self._refresher is not None:
            await self._refresher.refresh(person_id=ref.person_id)
        try:
            fix = await self._presence.latest_fix(
                circle_id=self._circle.circle_id, person_id=ref.person_id
            )
        except Exception:
            # The reader is documented never to be load-bearing; degrade to
            # unavailable, never an empty or invented position (R-5).
            log.exception("whereis read failed for %s", ref.person_id)
            fix = None
        if fix is None:
            return self._unavailable(
                f"I have no recorded location for {ref.name}. Say you can't see "
                "where they are right now — do not guess a place.",
                person=ref.name,
            )
        age_seconds = max(0, (now - fix.ts) // 1000)
        # The household's own name for the spot, when the fix sits inside a
        # saved place's geofence (design/23 §4.1, operator ask 2026-08-07):
        # «喺屋企» is the answer a person wants, and carrying place AND address
        # together means the model never guesses between them. Geometry runs
        # HERE, server-side; a failed places read just means no place name.
        place_name: str | None = None
        movement: dict[str, Any] = {}
        try:
            places = await self._presence.places_for(circle_id=self._circle.circle_id)
            for saved in places:
                if _within_place(fix.lat, fix.lng, saved):
                    place_name = saved.name
                    break
            # The movement read (§6 first slice): measured off the recent fix
            # history, and only when NOT inside a saved place — «at Home» and
            # «moving» cannot both be the headline, and containment wins.
            if place_name is None:
                recent = await self._presence.window(
                    circle_id=self._circle.circle_id, person_id=ref.person_id,
                    since_ts=now - _MOVEMENT_WINDOW_MS, until_ts=now,
                )
                movement = movement_view(recent, places)
        except Exception:
            log.exception("whereis derivation read failed — answering without it")
        if fix.is_driving:
            movement["driving"] = True
        return {
            "status": "ok",
            "person": ref.name,
            # The AGE is part of every answer (§5, design/14 §5.3: position is
            # not intent). A stale fix is honest and better than `unavailable`;
            # the model is told how old it is so it can say «20 分鐘前».
            "age_seconds": int(age_seconds),
            "wire_state": fix.wire_state.value,
            # In-group disclosure is fine (§5.3): the place name and/or address,
            # for Nora's REPLY. Raw coordinates are only here as a LAST fallback
            # when the fix carries neither; they still never leave in a tool
            # ARGUMENT (R-28, outbound stays hard-blocked).
            **({"place": place_name} if place_name else {}),
            **({"address": fix.address} if fix.address else {}),
            **({} if (place_name or fix.address) else
               {"lat": fix.lat, "lng": fix.lng}),
            **movement,
        }

    def _unavailable(self, note: str, **extra: Any) -> dict[str, Any]:
        return {"status": "unavailable", "note": note, **extra}
