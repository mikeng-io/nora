"""`whereis(person)` and its R-21 disclosure seam (design/19 §5, design/14 §5.2).

`whereis` reads the LOCAL presence record — never Life360 live — for one person
in this conversation, carries the fix's AGE to the model, and degrades to
`unavailable` (never null-island) when there is no usable fix. Nora reads a
local table that is always available; the collector daemon absorbs Life360's
fragility (§4), so `whereis` has no auth, no retry, no challenge-page to handle.

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

  **CAUTION for the engineer wiring this tool: today's R-28 block in
  `nora.agent.tools` does NOT cover this.** R-28 screens `scan_identifiers`
  (phone/UUID/key prefixes) and known person NAMES — a bare coordinate
  («22.31,114.16») and a place string match neither and would pass to
  `route()` → Google. So wiring `whereis` into a route-capable turn REQUIRES an
  explicit coordinate/place outbound barrier added alongside; it is not already
  there. This is council finding #4 (2026-08-07); the barrier lands when the
  tool is wired, not before (the seam is inert until then, so nothing leaks in
  the meantime).

Shipped as a testable SEAM, not yet wired into `NATIVE_TOOLS` — the same INERT
shape the navigation skill shipped in (DEC-48). Presence is `enabled=false`
(DEC-54/55); wiring `whereis` as a model-facing tool is a later step, so its
prompt weight and selection are not on the live surface until then.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol

from ..domain.presence import PresenceFix
from .room import Room

log = logging.getLogger("nora.agent.whereis")


class PresenceReader(Protocol):
    """The one read `whereis` needs from the presence store. Narrow on purpose,
    the same `_PrunableStore` shape `nora.app.runtime` uses: `whereis` programs
    against «the latest fix for this member», not the whole store."""

    async def latest_fix(
        self, *, circle_id: str, person_id: str
    ) -> PresenceFix | None: ...


@dataclass(frozen=True)
class Circle:
    """A Life360 circle as `whereis` needs it: its id and the set of `person_id`s
    in it. The `person_ids` are the disclosure roster — the set a conversation
    must be a SUBSET of. Built from `PresenceConfig` (the values of the
    circle→person_id map)."""

    circle_id: str
    person_ids: frozenset[str]


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
        metrics: Any = None,
    ) -> None:
        self._room = room
        self._circle = circle
        self._presence = presence
        self._guard = DisclosureGuard(circle)
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
        if view.degraded or view.unnamed > 0 or not self._guard.permits(room_person_ids):
            if self._metrics is not None:
                self._metrics.inc("nora_presence_disclosure_refusals_total")
            log.warning(
                "whereis refused: conversation is not a verified subset of circle %s "
                "(degraded=%s)",
                self._circle.circle_id,
                view.degraded,
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
        return {
            "status": "ok",
            "person": ref.name,
            # The AGE is part of every answer (§5, design/14 §5.3: position is
            # not intent). A stale fix is honest and better than `unavailable`;
            # the model is told how old it is so it can say «20 分鐘前».
            "age_seconds": int(age_seconds),
            "wire_state": fix.wire_state.value,
            # In-group disclosure is fine (§5.3): the address/place, for Nora's
            # REPLY. Raw coordinates are only here as a fallback when the fix did
            # not reverse-geocode; they still never leave in a tool ARGUMENT
            # (R-28, outbound stays hard-blocked).
            **({"address": fix.address} if fix.address else
               {"lat": fix.lat, "lng": fix.lng}),
        }

    def _unavailable(self, note: str, **extra: Any) -> dict[str, Any]:
        return {"status": "unavailable", "note": note, **extra}
