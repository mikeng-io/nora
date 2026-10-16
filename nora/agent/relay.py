"""`Relay` — the one way anything Nora is told reaches a room she is not in.

R-2 says a turn's messages all go to the tagging conversation, and that holds
for `send_reply`. This is the deliberate, gated exception (DEC-105): somebody in
one room asks her to tell another, and she does — under their name, never her
own.

**Why this is not merely a second sender.** Every other guardrail is scoped to
ONE conversation: `policy.toml` decides who may speak in a room, R-31 decides
what may be disclosed into it, `where_is` refuses in a room holding an account
it cannot name. A relay reaches past all of that by construction — the words
appear in front of an audience that made its own trust decisions. So the gate is
not "is this allowed here", it is "are these two rooms the same group of people,
and is the asker one of them".

The five refusals, all fail-closed, in the order they are cheapest to decide:

1. **An unmapped asker gets nothing.** A person Nora cannot name cannot be
   attributed, and an unattributed relay is what this module exists to prevent.
2. **The destination is named by PLATFORM, not by room** — «tell Signal». A
   unit holds at most one room per platform, so within a unit the platform IS
   the address. Never an id: a model that could name one could be talked into
   naming one. Two rooms of one platform in one unit REFUSES rather than
   picking: that violates the invariant, and guessing would turn a
   misconfiguration into a delivery to the wrong group.
3. **Both rooms must share a `unit`** (`nora.policy.Conversation`). Rooms in one
   unit are one social unit reachable in more than one place (a friend group
   with a Signal group AND a Discord channel), so the audience is already the
   same. Rooms in different units are different groups of people, however
   trusted each is on its own.
4. **The asker must independently be allowed in the DESTINATION.** The unit
   bounds which rooms may be paired; this decides who may use the pair. Without
   it, anyone in one room of a unit could speak into every other room of it,
   which is a promotion, not a relay.
5. **The text must pass the same egress scan a reply does** (R-18). It is
   outbound text authored partly by a person and partly by a model, and the
   destination is a room that never saw either.

**A network that is not up is not tried.** Once the five gates pass, the
destination network's transport is read at call time through the SAME
`PresenceReader.state` that renders `<presence>`, and anything but `up` answers
`unavailable` without speaking. A mechanical refusal is used because a model
told «Discord is down» still called `send_to`. It runs after the gates because
they are policy and free, and a person not allowed in a room is told that, not
how its network is doing. The note names the state in `<presence>`'s own words
and never the adapter's `detail`, which is the operator's diagnostic and may
name an account.

**It does not send. It occasions a fire turn** (DEC-99): speaking into a room
nobody tagged her in is what `nora.app.fire.FirePath` already is, and a relay is
its fourth occasion. Calling `adapter.send` here would bypass R-2's latch, R-4,
R-6's leak scan, R-18's egress scan and A9's audit-first write — and the last
bites quietest: the message would appear in the destination with no row
recording it, leaving a hole in that room's transcript Nora cannot see. This
module therefore holds a `speak` callable rather than an adapter.

**Attribution is not decoration.** What arrives always names who asked. Nora
saying «dinner at 8» and Nora saying «Miles says: dinner at 8» are different
claims to the people reading them, and only the second is true. Without it the
tool puts words into a room under her name, the strongest voice in the room.

Send-only: nothing mirrors, nothing subscribes, and a relayed message is not
itself relayable. A bridge would need its own answers about loops and about
what the destination's transcript claims.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from ..domain.models import PersonRef
from ..domain.self_presence import TransportState
from ..language.presence_render import state_phrase
from ..policy import Conversation, Policy
from .egress import scan_reply

log = logging.getLogger("nora.agent.relay")

RELAY_METRIC = "nora_relay_total"


def _key(name: str) -> str:
    return " ".join(name.split()).casefold()


class Relay:
    """Resolves a room by label, checks the five gates, and sends. Nothing else.

    Holds process-lifetime collaborators only: the allowlist, the adapters, the
    metrics sink. Per-turn facts (who asked, which room they are in) arrive as
    arguments, so one instance serves every turn and holds nothing about any of
    them.
    """

    def __init__(
        self,
        *,
        policy: Policy,
        speak: Any = None,
        slots: Any = None,
        occasion_for: Any = None,
        transport: Callable[[str], Awaitable[TransportState]],
        metrics: Any = None,
    ) -> None:
        self._policy = policy
        # `PresenceReader.state`: one network's transport, read now, bounded
        # by the reader's own timeout, and `unknown` (never `up`) when the
        # read raises. Required, no default: a relay that cannot ask whether
        # the destination is up can only refuse everything, so an unwired one
        # is a construction error.
        self._transport = transport
        # `FirePath.speak`, the same callable `ComputeJobPoller` is given.
        # `None` is the not-configured state of the offline and eval
        # compositions: `send_to` then answers `unavailable`, never a silent
        # no-op.
        self._speak = speak
        # The SAME `ConversationSlots` every other path into a conversation
        # holds. A relay speaks into a room that may be mid-turn with somebody
        # else; a second instance would be a second lock and no serialisation.
        self._slots = slots
        self._occasion_for = occasion_for
        self._metrics = metrics

    def _count(self, outcome: str) -> None:
        if self._metrics is not None:
            self._metrics.inc(RELAY_METRIC, outcome=outcome)

    def _refuse(self, outcome: str, note: str) -> dict:
        self._count(outcome)
        return {"status": "refused", "note": note}

    def _find(self, platform: str, *, unit: str, exclude: str) -> tuple[Conversation, ...]:
        """The room of `platform` inside `unit`, excluding the source.

        Returns a tuple so the caller can tell none from more than one: a
        unit holding two rooms of one platform breaks the invariant this
        addressing scheme rests on, and it must refuse rather than choose.
        """
        wanted = _key(platform)
        if not wanted:
            return ()
        return tuple(
            c for c in self._policy.conversations
            if _key(c.platform) == wanted
            and c.unit == unit
            and c.primary_id != exclude
        )

    async def send(
        self,
        *,
        platform: str,
        text: str,
        requester: PersonRef | None,
        from_platform: str,
        from_conversation_id: str,
    ) -> dict:
        """Relay `text` into the `platform` room of the source unit, as `requester`."""
        body = (text or "").strip()
        if not body:
            return self._refuse(
                "empty", "There was nothing to pass on. Say so rather than sending nothing."
            )
        if requester is None:
            return self._refuse(
                "unmapped",
                "I don't know who you are well enough to pass something on in your "
                "name. Say you can't, and do not send it unattributed.",
            )

        source = self._policy.conversation(from_platform, from_conversation_id)
        if source is None:
            # Ingest admitted this turn from this room, so this is a wiring
            # fault; with no source there is no unit to compare, so refuse.
            log.error(
                "relay refused: source %s/%s is not in policy",
                from_platform, from_conversation_id,
            )
            return self._refuse(
                "unknown_source", "I can't tell which room I'm in well enough to do that."
            )

        if _key(platform) == _key(source.platform):
            return self._refuse(
                "same_room",
                "That's where we already are: just say it here instead of "
                "passing it on.",
            )

        # Scoped to the SOURCE's unit from the start: a room belonging to
        # another group is never a candidate, so there is no way to name it and
        # be told no. It does not exist from here.
        matches = self._find(
            platform, unit=source.unit, exclude=source.primary_id,
        )
        if not matches:
            log.warning(
                "relay refused: no %r room in unit %r", platform, source.unit,
            )
            return self._refuse(
                "unknown_room",
                "There's no such place I can reach from here. Say so rather than "
                "guessing at one.",
            )
        if len(matches) > 1:
            log.error(
                "relay refused: unit %r holds %d %r rooms — a unit is "
                "meant to hold at most one room per platform, and with two there "
                "is no way to tell which was meant",
                source.unit, len(matches), platform,
            )
            return self._refuse(
                "ambiguous_room",
                "There's more than one of those and I can't tell which they mean. "
                "Ask them.",
            )
        destination = matches[0]

        if not self._policy.is_allowed(
            platform=destination.platform,
            conversation_id=destination.primary_id,
            person_id=requester.person_id,
        ):
            return self._refuse(
                "not_allowed_there",
                "They're not in that room themselves, so I won't speak into it for "
                "them. Say so plainly.",
            )

        # Attributed, always (module docstring). Built here rather than left to
        # the model so it cannot be talked out of it.
        outbound = f"{requester.name}: {body}"

        if (kind := scan_reply(outbound)) is not None:
            log.warning("relay blocked by egress scan (%s)", kind)
            return self._refuse(
                "egress_blocked",
                "There's something in that I can't repeat into another room. Say so.",
            )

        # Read per call, never cached: a network that was up when the turn began
        # can be down by the time she relays.
        state = await self._transport(destination.platform)
        if state != "up":
            self._count("transport_down")
            log.warning(
                "relay refused: %s transport is %s", destination.platform, state,
            )
            # `reconnecting` is the one state expected to heal on its own, so
            # only there is offering to try later honest.
            later = (
                " It usually comes back by itself, so you can offer to try "
                "again later."
                if state == "reconnecting" else ""
            )
            return {
                "status": "unavailable",
                "note": f"{state_phrase(destination.platform, state)}, so nothing "
                        "was sent. Tell them here it didn't go; do not say it "
                        f"went or promise it will.{later}",
            }

        if self._speak is None or self._slots is None or self._occasion_for is None:
            self._count("no_adapter")
            return {
                "status": "unavailable",
                "note": "I can't reach that room right now. Say you couldn't send it.",
            }

        # R-10's per-conversation lock, for the DESTINATION. `try_acquire`, not
        # `acquire` (the heartbeat's rule): a room already mid-turn is not one to
        # interrupt, and waiting would hold this turn open while another runs.
        key = (destination.platform, destination.primary_id)
        if not self._slots.try_acquire(key):
            self._count("busy")
            return {
                "status": "unavailable",
                "note": "They're mid-conversation with me over there. Say it didn't "
                        "go and they can ask again.",
            }
        try:
            outcome = await self._speak(
                self._occasion_for(
                    destination=destination,
                    requester=requester,
                    text=outbound,
                    from_platform=from_platform,
                )
            )
        finally:
            # One call in a `finally` (the tag path's and the tick's rule): a
            # turn that dies must not strand whatever queued behind it.
            self._slots.release_and_dispatch(key)

        if outcome != "sent":
            self._count("failed")
            return {
                "status": "unavailable",
                "note": "I couldn't get that through. Say it didn't send, and do not "
                        "claim it did.",
            }
        self._count("ok")
        # `send_to`'s «tell the asker whether it went» rides with the answer it
        # is about (ADR 0015); every refusal above already says what to say.
        return {
            "status": "ok", "room": destination.label,
            "note": "It went. Tell the person here, with send_reply.",
        }
