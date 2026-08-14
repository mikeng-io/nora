"""`Relay` — the one way anything reaches a room Nora is not in (DEC-105/R-49).

Every test here is a REFUSAL except two. That ratio is the design: a relay
reaches past every guardrail this codebase scopes to one conversation, so what
matters is not that it can send but that it declines in each of the ways it
must.

The double records the OCCASION it was asked to speak rather than a send: the
relay does not touch an adapter, it occasions a fire turn into the destination
(DEC-99's ruling, so R-1b's three links hold and the relayed message is audited
into the room it lands in). What a test asserts on is therefore which room the
occasion names and what it carries.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from nora.agent.relay import Relay
from nora.domain.models import PersonRef
from nora.policy import Conversation, Policy

MILES = PersonRef(person_id="miles_novak", name="Miles", honcho_peer_id="miles_novak")
ADA = PersonRef(person_id="ada", name="Ada", honcho_peer_id="ada")

SIGNAL_ROOM = "Z3JvdXA="
DISCORD_ROOM = "111/222"
OTHER_ROOM = "999/888"


class _FakeFire:
    """Records what it was asked to speak, and where."""

    def __init__(self, *, outcome: str = "sent") -> None:
        self.spoken: list[tuple[str, str, str]] = []
        self._outcome = outcome

    async def speak(self, occasion) -> str:
        self.spoken.append(
            (occasion.platform, occasion.conversation_id, occasion.body)
        )
        return self._outcome


class _FakeSlots:
    """R-10's lock, recording acquire/release so a test can prove the
    destination's slot was TAKEN and given back."""

    def __init__(self, *, free: bool = True) -> None:
        self.free = free
        self.acquired: list[tuple] = []
        self.released: list[tuple] = []

    def try_acquire(self, key) -> bool:
        if not self.free:
            return False
        self.acquired.append(key)
        return True

    def release_and_dispatch(self, key) -> None:
        self.released.append(key)


def _occasion_for(*, destination, requester, text, from_platform):
    """Stands in for `nora.app.fire.relay_occasion`, carrying only what these
    tests assert on."""
    return SimpleNamespace(
        platform=destination.platform,
        conversation_id=destination.primary_id,
        body=text,
        person_id=requester.person_id,
    )


def _policy(*, other_workspace: bool = False, ada_in_discord: bool = False) -> Policy:
    """Two rooms in one workspace by default — the legitimate pairing."""
    signal = Conversation(
        platform="signal", ids=frozenset({SIGNAL_ROOM}),
        persons=frozenset({"miles_novak"}), label="Friends",
        primary_id=SIGNAL_ROOM, workspace="friends",
    )
    discord = Conversation(
        platform="discord", ids=frozenset({DISCORD_ROOM}),
        persons=frozenset({"miles_novak", "ada"} if ada_in_discord else {"miles_novak"}),
        label="Zone 42", primary_id=DISCORD_ROOM,
        workspace="ridgeline" if other_workspace else "friends",
    )
    rooms = (signal, discord)
    return Policy(
        conversations=rooms,
        _by_id={(c.platform, cid): c for c in rooms for cid in c.ids},
    )


def _relay(policy: Policy, fire=None, slots=None, metrics=None):
    fire = fire if fire is not None else _FakeFire()
    slots = slots if slots is not None else _FakeSlots()
    relay = Relay(
        policy=policy, speak=fire.speak, slots=slots,
        occasion_for=_occasion_for, metrics=metrics,
    )
    return relay, fire, slots


async def _send(relay, **kw):
    base = {
        "platform": "signal", "text": "we're starting", "requester": MILES,
        "from_platform": "discord", "from_conversation_id": DISCORD_ROOM,
    }
    return await relay.send(**{**base, **kw})


# ---------------------------------------------------------------------------
# the two that go through
# ---------------------------------------------------------------------------


async def test_a_relay_reaches_the_named_room_and_only_that_room():
    relay, fire, slots = _relay(_policy())

    out = await _send(relay)

    assert out["status"] == "ok"
    assert fire.spoken == [("signal", SIGNAL_ROOM, "Miles: we're starting")]
    assert slots.acquired == [("signal", SIGNAL_ROOM)], (
        "the DESTINATION's slot must be taken — it may be mid-turn with somebody else"
    )
    assert slots.released == slots.acquired, "and given back on every exit path"


async def test_what_arrives_always_says_who_asked():
    """Attribution is not decoration. Nora saying «dinner at 8» and Nora saying
    «Miles says dinner at 8» are different claims to the people reading them,
    and only the second is true. Without it this tool is a way to put words
    into a room under her name — the strongest voice there, borrowed."""
    relay, fire, _ = _relay(_policy())

    await _send(relay, text="dinner at 8")

    (_, _, sent), = fire.spoken
    assert sent.startswith("Miles:"), sent
    assert "dinner at 8" in sent


# ---------------------------------------------------------------------------
# the refusals
# ---------------------------------------------------------------------------


async def test_an_unmapped_asker_is_refused_rather_than_sent_unattributed():
    """A person Nora cannot name cannot be attributed, and an unattributed
    relay is the exact thing attribution exists to prevent."""
    relay, fire, _ = _relay(_policy())

    out = await _send(relay, requester=None)

    assert out["status"] == "refused"
    assert fire.spoken == []


async def test_a_room_in_another_workspace_is_refused():
    """THE BOUNDARY. Rooms sharing a workspace are one social unit reachable in
    more than one place, so the audience is already the same audience. Rooms in
    different workspaces are different groups of people, and no amount of
    per-room trust makes speaking across them acceptable."""
    relay, fire, _ = _relay(_policy(other_workspace=True))

    out = await _send(
        relay, platform="discord", from_platform="signal",
        from_conversation_id=SIGNAL_ROOM,
    )

    assert out["status"] == "refused"
    assert fire.spoken == []


async def test_somebody_not_in_the_destination_cannot_speak_into_it():
    """The workspace bounds WHICH rooms may be paired; this decides who may use
    the pair. Without it anyone in one room of a workspace could speak into
    every other room of it — a promotion, not a relay.

    Ada is in the Discord room and NOT in the Signal one.
    """
    relay, fire, _ = _relay(_policy(ada_in_discord=True))

    out = await _send(relay, requester=ADA)

    assert out["status"] == "refused"
    assert fire.spoken == []


async def test_an_unknown_room_name_is_refused_not_guessed():
    relay, fire, _ = _relay(_policy())

    out = await _send(relay, platform="telegram")

    assert out["status"] == "refused"
    assert fire.spoken == []


async def test_two_rooms_of_one_platform_in_a_workspace_refuse_rather_than_pick():
    """A workspace holds at most one room per platform BY DESIGN, which is what
    makes the platform a usable address. Two is the invariant broken, and
    guessing would turn a misconfiguration into a delivery to the wrong group.
    """
    dupe = Conversation(
        platform="signal", ids=frozenset({OTHER_ROOM}), persons=frozenset({"miles_novak"}),
        label="Friends Overflow", primary_id=OTHER_ROOM, workspace="friends",
    )
    base = _policy()
    rooms = (*base.conversations, dupe)
    policy = Policy(
        conversations=rooms,
        _by_id={(c.platform, cid): c for c in rooms for cid in c.ids},
    )
    relay, fire, _ = _relay(policy)

    out = await _send(relay)

    assert out["status"] == "refused"
    assert fire.spoken == []


async def test_relaying_into_this_very_room_is_refused():
    relay, fire, _ = _relay(_policy())

    out = await _send(relay, platform="discord")

    assert out["status"] == "refused"
    assert fire.spoken == []


async def test_text_that_fails_the_egress_scan_never_leaves():
    """R-18 applies to a relay for the reason it applies to a reply, and more
    so: the destination is a room that saw neither the asker nor the turn."""
    relay, fire, _ = _relay(_policy())

    out = await _send(relay, text="call him on +85290000101")

    assert out["status"] == "refused"
    assert fire.spoken == []


async def test_a_platform_with_no_adapter_is_unavailable_not_refused():
    """R-27's distinction: «I cannot reach that room» is a wiring fact, not a
    policy decision, and reporting it as a refusal would tell the group they
    were not allowed when the truth is that Nora could not."""
    relay, _f, _ = _relay(_policy(), fire=None)
    relay = Relay(policy=_policy(), speak=None, slots=None, occasion_for=None)

    out = await _send(relay)

    assert out["status"] == "unavailable"


async def test_a_failed_send_is_never_reported_as_delivered():
    relay, _f, _ = _relay(_policy(), fire=_FakeFire(outcome="failed_error"))

    out = await _send(relay)

    assert out["status"] == "unavailable"


async def test_empty_text_sends_nothing():
    relay, fire, _ = _relay(_policy())

    out = await _send(relay, text="   ")

    assert out["status"] == "refused"
    assert fire.spoken == []


@pytest.mark.parametrize(
    ("kwargs", "outcome"),
    [
        ({"requester": None}, "unmapped"),
        ({"platform": "telegram"}, "unknown_room"),
        ({"platform": "discord"}, "same_room"),
        ({"text": "+85290000101"}, "egress_blocked"),
        ({}, "ok"),
    ],
)
async def test_every_outcome_is_counted_by_name(kwargs, outcome):
    """`nora_relay_total{outcome}` is how an operator sees somebody trying to
    speak past a trust boundary. A refusal that is not counted is a refusal
    nobody learns about."""
    from nora.obs.metrics import Metrics

    metrics = Metrics()
    relay, _f, _ = _relay(_policy(), metrics=metrics)

    await _send(relay, **kwargs)

    assert metrics.get("nora_relay_total", outcome=outcome) == 1.0
