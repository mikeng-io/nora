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
import time
from dataclasses import dataclass
from typing import Any, Protocol

from ..domain.models import PersonRef
from ..domain.ports import ReverseGeocoder
from ..domain.presence import PresenceFix, SavedPlace, haversine_distance_m
from .room import Room, find_by_name

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
    # The same members, RESOLVED, so a name can be matched against them.
    # `person_ids` answers "may this room be told?"; this answers "who did they
    # mean?" — and until R-47 the second question was asked of the ROOM, so a
    # circle member who was not in the group could not be asked about at all.
    people: tuple[PersonRef, ...] = ()


def _dedupe(refs: tuple[PersonRef, ...]) -> tuple[PersonRef, ...]:
    """First occurrence per `person_id`, order preserved.

    The circle and the room overlap for anyone who is both a member and
    present, and a duplicate would render as `ambiguous` — «more than one
    person here goes by that» — about ONE person.
    """
    seen: set[str] = set()
    out: list[PersonRef] = []
    for ref in refs:
        if ref.person_id in seen:
            continue
        seen.add(ref.person_id)
        out.append(ref)
    return tuple(out)


# The movement read (design/19 §6, first slice — operator ask 2026-08-07):
# MEASURED facts off the stored fix history, never a prediction. A member is
# `moving` when their last two fixes inside this window are further apart than
# the noise floor; `towards` names a saved place only when the distance to it
# CLOSED by more than the floor across those fixes — «getting nearer to Home»
# is a measurement, «going home» is a guess, and only the first ships (R-5).
_MOVEMENT_WINDOW_MS = 15 * 60_000
_MOVEMENT_FLOOR_M = 150.0

# Below this, Life360's own reported speed is GPS jitter off a stationary or
# slow-walking phone, not a measurement worth relaying (operator ask
# 2026-08-16) — below brisk-walking pace. Omitted, never rendered as `0`.
_SPEED_FLOOR_KMH = 3.0


def movement_view(
    fixes: list[PresenceFix], places: list[SavedPlace],
) -> dict[str, Any]:
    """`moving` / `towards` off the last two fixes in the window — pure,
    server-side, and empty when there is only one fix (one point has no
    direction; absence of the keys IS the honest «cannot tell»)."""
    if len(fixes) < 2:
        return {}
    prev, last = fixes[-2], fixes[-1]
    moved = haversine_distance_m(prev.lat, prev.lng, last.lat, last.lng)
    out: dict[str, Any] = {"moving": moved > _MOVEMENT_FLOOR_M}
    if not out["moving"]:
        return out
    best_name, best_closed = None, _MOVEMENT_FLOOR_M
    for place in places:
        closed = (
            haversine_distance_m(prev.lat, prev.lng, place.lat, place.lng)
            - haversine_distance_m(last.lat, last.lng, place.lat, place.lng)
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
    return haversine_distance_m(lat, lng, place.lat, place.lng) <= place.radius_m


async def resolvable_places(
    *, room: Room, circles: tuple[Circle, ...], presence: PresenceReader,
    location_trusted: bool = False,
) -> tuple[SavedPlace, ...] | None:
    """The saved places THIS room may resolve by name (design/23 §4.1): only
    from circles the room is a verified subset of, and EMPTY on doubt about the
    ROOM — degraded room, unmapped account, guard refusal. The caller falls
    through to the public lookup rather than refusing: a hard refusal would
    confirm a guarded name exists, and fall-through makes it indistinguishable
    from an unsaved one (R-21/R-35's own shape).

    `None` is the FOURTH answer, and it is not the same as `()` (R-27, both
    2026-08-10 reviews): the read itself failed — a presence store that is
    unreachable, a `places_for` that raised. `()` says «no names you may have»,
    `None` says «I could not check», and collapsing them meant an unreachable
    Postgres rendered as 「你冇 save 過任何地方」 — a confident wrong answer with a
    healthy `status="ok"` beside it. An ERROR is never a PASS.

    `location_trusted` carries DEC-79 here, because this is the SECOND
    disclosure surface and the first review found it stranded: `where_is` was
    widened while this was not, so in the operator's own room `where_is`
    disclosed a street address while `saved_places` answered «no saved places»
    forever — the same four unmappable accounts, killing the very path DEC-77/78
    spent two commits routing 「返工」 onto. One room-level switch, every location
    surface. It relaxes `unnamed` exactly as `Whereis.locate` does, and
    `degraded`/unavailable stay hard for the same reason they do there."""
    try:
        view = await room.scope()
        unnamed_blocks = view.unnamed > 0 and not location_trusted
        if not view.available or view.degraded or unnamed_blocks:
            return ()
        room_ids = frozenset(p.person_id for p in view.people)
        out: list[SavedPlace] = []
        for circle in circles:
            if location_trusted or DisclosureGuard(circle).permits(room_ids):
                out.extend(await presence.places_for(circle_id=circle.circle_id))
        return tuple(out)
    except Exception:
        log.exception("resolvable_places failed — cannot say whether names exist")
        return None


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
        Circle(
            circle_id=circle_id,
            person_ids=frozenset(person_ids),
            people=tuple(
                ref for pid in sorted(person_ids)
                if (ref := persons.by_person.get(pid)) is not None
            ),
        )
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
    *,
    circle: Circle,
    group_person_ids: frozenset[str],
    metrics: Any = None,
    platform: str = "",
    conversation: str = "",
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
    which SEES `view.unnamed > 0` — an unmapped account the coarse person_id
    subset here cannot; this startup check is a belt-and-braces pre-flight over
    the KNOWN person_ids.

    That per-call guard refuses on `unnamed` in an untrusted room, and WAIVES it
    (counted, never silent) in a location-trusted one since DEC-79 — so this
    check being green has never implied the room is fully mapped, and now does
    not imply an unmapped account would be refused either."""
    ok = bool(group_person_ids) and group_person_ids <= circle.person_ids
    if metrics is not None:
        metrics.gauge(
            "nora_presence_circle_subset_ok", 1.0 if ok else 0.0,
            circle=circle.circle_id, platform=platform, conversation=conversation,
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
        geocoder: ReverseGeocoder | None = None,
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
        # Optional reverse-geocode, ON DEMAND (moved here from the collector,
        # 2026-08-14): a fix with no Life360 address is geocoded ONLY when
        # someone actually asks `where_is` about it, not on every poll cycle
        # for every tracked person regardless of whether anyone ever reads the
        # result — the collector's own eager version cost real, billed Google
        # calls for fixes nobody asked about (~12k requests / 6 days, ~$60,
        # confirmed against Cloud Monitoring's own request-count metric before
        # this moved). The coordinate this sends is Nora's own code reading a
        # DB row, never a model-authored value — the same "not the model, not
        # the group" ground `nora.infra.geocode`'s own docstring already
        # argues for the collector's version of this exact call.
        self._geocoder = geocoder
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
          - `refused` — a POLICY answer, and only ever that: this conversation
            is not a subset of the circle, or it holds an account Nora cannot
            name (R-21 fail-closed) — never a silent omission. A room that is
            location-trusted (DEC-79) reaches neither, so it never sees this.
          - `unavailable` — the room could not be READ (unavailable OR degraded
            roster), or there is no usable fix. The split from `refused` is
            R-27: an ERROR is never dressed as a policy decision, because
            «someone here is outside the circle» is a claim about the room and
            a wedged signal-cli is not. Never null-island, never a guess.
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
        # fails the subset test — fail-closed on any uncertainty, and TWO of the
        # three are unconditional (DEC-79 made the third overridable; the list
        # below is written as it now behaves, not as DEC-74 left it):
        #   - a RESOLVED member whose person_id is not in the circle. Refuses
        #     unless the room is location-trusted (DEC-74);
        #   - `view.unnamed > 0` — an account in the room resolving to NO
        #     person_id is by definition unverifiable against the circle, and it
        #     is the LIKELIEST real trigger (a stranger not in identity.toml joins
        #     the group, design/19 §5's own example). `view.people` is only the
        #     RESOLVABLE subset, so checking it alone would silently disclose past
        #     an unmapped outsider (both #37 reviews, 2026-08-07). Refuses unless
        #     the room is location-trusted — and when it is waived, it is COUNTED
        #     and LOGGED rather than passing silently, see below;
        #   - a DEGRADED view (tagger-only, roster unreadable) that cannot verify
        #     the subset at all. UNCONDITIONAL, and it answers `unavailable`
        #     rather than `refused` — see the branch itself for why.
        room_person_ids = frozenset(p.person_id for p in view.people)
        # DEC-79 widened DEC-74: `location = true` relaxes the SUBSET check AND
        # the unnamed refusal, because the operator's real rooms permanently hold
        # sealed-sender accounts (no phone number on the wire, never spoken, so
        # nothing to key `identity.toml` on) and a per-condition flag only asked
        # him the same question twice. `degraded` still refuses for everyone:
        # trust can overrule what a roster SAYS, never the absence of a roster.
        subset_ok = self._location_trusted or self._guard.permits(room_person_ids)
        unnamed_blocks = view.unnamed > 0 and not self._location_trusted
        # A DEGRADED room is an ERROR, not a policy decision, and after DEC-79 it
        # is the ONLY refusal a trusted room can reach — so reporting it as
        # `refused` put a FALSE sentence in Nora's mouth on the operator's own
        # live failure mode (R-27, fail-closed audit 2026-08-10). signal-cli
        # wedged and `listGroups` timing out would render to the group as
        # 「有人唔喺個 circle 入面」 — a claim about who is in the room — when the
        # truth is «the daemon is down». `unavailable` is that truth, and the
        # metric keeps `reason="degraded"` so the refusal stays diagnosable.
        if view.degraded:
            if self._metrics is not None:
                self._metrics.inc(
                    "nora_presence_disclosure_refusals_total", reason="degraded"
                )
            log.warning(
                "whereis could not verify the room (degraded): circle=%s "
                "location_trusted=%s", self._circle.circle_id, self._location_trusted,
            )
            return self._unavailable(
                "I can't confirm who is in this group right now, so I can't say "
                "where anyone is. Say you couldn't check — do not guess, and do "
                "not blame anyone's sharing settings."
            )
        if unnamed_blocks or not subset_ok:
            # WHICH condition fired — so a refusal is diagnosable from the
            # trace/metric instead of inferred (obs review 2026-08-08).
            reason = "unnamed" if unnamed_blocks else "not_subset"
            if self._metrics is not None:
                self._metrics.inc(
                    "nora_presence_disclosure_refusals_total", reason=reason
                )
            log.warning(
                "whereis refused (%s): circle=%s unnamed=%d subset_ok=%s "
                "location_trusted=%s",
                reason, self._circle.circle_id, view.unnamed,
                subset_ok, self._location_trusted,
            )
            return {
                "status": "refused",
                "note": "I can't share where people are in this conversation — "
                        "someone here is outside the location-sharing circle, or I "
                        "can't confirm who is here. Say so; do not guess.",
            }
        # A WAIVED guard is counted, not silent (both 2026-08-10 reviews). DEC-79's
        # entire diagnosis was `…refusals_total{reason="unnamed"} 12`; relaxing the
        # condition without recording the waiver would have retired the instrument
        # that found the bug, leaving a zero refusal counter unable to distinguish
        # «the flag is working» from «the room filled up with strangers». The
        # startup pre-flight warns once at boot and never again, so an account that
        # joins later would otherwise produce no signal at all for the process
        # lifetime. `unnamed` is a LABEL VALUE, not a count, so the cardinality
        # stays fixed; the count goes in the log line beside it.
        if view.unnamed > 0 and self._location_trusted:
            if self._metrics is not None:
                self._metrics.inc(
                    "nora_presence_disclosure_waived_total", reason="unnamed"
                )
            log.warning(
                "whereis disclosed into a location-trusted room holding %d unmapped "
                "account(s) (DEC-79 waiver): circle=%s — `nora persons pending` "
                "cannot see accounts that have never spoken",
                view.unnamed, self._circle.circle_id,
            )
        # R-47: resolved against the CIRCLE, not just the room. The room is the
        # AUDIENCE gate and it has already been cleared above; who may be ASKED
        # ABOUT is a different question, and scoping it to room membership meant
        # a circle member who simply is not in this group — sharing her location
        # with Nora the whole time — answered «nobody here goes by that». The
        # room's own people are unioned in so somebody present but OUTSIDE the
        # circle still resolves, and gets the honest «I have no recorded
        # location for them» rather than «who?».
        matches = _dedupe(find_by_name(self._circle.people, person) + view.find(person))
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
        # Life360's own reported ground speed for THIS fix's instant — not a
        # 2-fix derivation, so unlike `moving`/`towards` it needs no window
        # read and is available on a person's very first recorded fix.
        # Suppressed inside a saved place for the same reason `moving`/
        # `towards` are (§6's "cannot both be the headline"): indoor/urban
        # GPS jitter routinely clears the floor below, and «at Home» is a
        # more useful answer than a spurious walking-pace reading.
        if (
            place_name is None
            and fix.speed_kmh is not None
            and fix.speed_kmh > _SPEED_FLOOR_KMH
        ):
            movement["speed_kmh"] = round(fix.speed_kmh, 1)
        # ON-DEMAND reverse-geocode: only reached when there is no saved-place
        # name AND Life360 gave no address of its own — the two cheaper answers
        # above always win first. This is the ONE live external call `locate`
        # makes, and it is paid for only by an actual question about an actual
        # fix, not by a background poll cycle nobody asked about.
        address = fix.address
        if place_name is None and address is None and self._geocoder is not None:
            try:
                address = await self._geocoder.address_for(lat=fix.lat, lng=fix.lng)
            except Exception:
                log.exception("whereis on-demand geocode failed — answering without it")
        return {
            "status": "ok",
            "person": ref.name,
            # The AGE is part of every answer (§5, design/14 §5.3: position is
            # not intent). A stale fix is honest and better than `unavailable`;
            # the model is told how old it is so it can say «20 分鐘前».
            "age_seconds": int(age_seconds),
            "wire_state": fix.wire_state.value,
            # DEC-156: lat/lng ALWAYS carried — the model may pass them to a
            # street-view/maps fetch tool for an exact location (Miles's
            # request 2026-09-05). They never enter a REPLY (R-28's speech
            # half stands; the docstring teaches the model the same rule).
            **({"place": place_name} if place_name else {}),
            **({"address": address} if address else {}),
            "lat": fix.lat,
            "lng": fix.lng,
            **movement,
        }

    def _unavailable(self, note: str, **extra: Any) -> dict[str, Any]:
        return {"status": "unavailable", "note": note, **extra}
