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

import asyncio
from types import SimpleNamespace

import pytest

from nora.agent.relay import Relay
from nora.domain.models import PersonRef
from nora.domain.ports import AdapterHealth
from nora.engagement.self_presence import PresenceReader
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


#: The `detail` a degraded adapter reports. It is the operator's diagnostic
#: and here it names an account, so it must never reach her.
_DETAIL = "instagram session for nora_burner_account is 'checkpointed'"


class _Health:
    """One adapter's `health()`, scripted per call and counted.

    `script` is consumed one entry per call (the last entry repeats), so a test
    can make a network go down or come back between two relays. An entry is a
    state name: `up`, `disconnected`, `degraded`, `raises` or `hangs`.
    """

    def __init__(self, platform: str, *script: str) -> None:
        self.platform = platform
        self.script = list(script or ("up",))
        self.calls = 0

    async def health(self) -> AdapterHealth:
        entry = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        if entry == "raises":
            raise RuntimeError(f"bridge unreachable: {_DETAIL}")
        if entry == "hangs":
            await asyncio.sleep(3600)
        return AdapterHealth(
            platform=self.platform,
            connected=entry == "up",
            degraded=entry == "degraded",
            detail=_DETAIL if entry == "degraded" else "",
        )


def _reader(policy: Policy, **adapters) -> PresenceReader:
    """The SAME reader `<presence>` renders from, over scripted adapters. Every
    network is up unless a test says otherwise; `None` removes one."""
    base = {"signal": _Health("signal"), "discord": _Health("discord")}
    base.update(adapters)
    return PresenceReader(
        policy=policy,
        adapters={k: v for k, v in base.items() if v is not None},
        timeout_seconds=0.05,
    )


def _occasion_for(*, destination, requester, text, from_platform):
    """Stands in for `nora.app.fire.relay_occasion`, carrying only what these
    tests assert on."""
    return SimpleNamespace(
        platform=destination.platform,
        conversation_id=destination.primary_id,
        body=text,
        person_id=requester.person_id,
    )


def _policy(*, other_unit: bool = False, ada_in_discord: bool = False) -> Policy:
    """Two rooms in one unit by default — the legitimate pairing."""
    signal = Conversation(
        platform="signal", ids=frozenset({SIGNAL_ROOM}),
        persons=frozenset({"miles_novak"}), label="Friends",
        primary_id=SIGNAL_ROOM, unit="friends",
    )
    discord = Conversation(
        platform="discord", ids=frozenset({DISCORD_ROOM}),
        persons=frozenset({"miles_novak", "ada"} if ada_in_discord else {"miles_novak"}),
        label="Zone 42", primary_id=DISCORD_ROOM,
        unit="ridgeline" if other_unit else "friends",
    )
    rooms = (signal, discord)
    return Policy(
        conversations=rooms,
        _by_id={(c.platform, cid): c for c in rooms for cid in c.ids},
    )


def _relay(policy: Policy, fire=None, slots=None, metrics=None, reader=None):
    fire = fire if fire is not None else _FakeFire()
    slots = slots if slots is not None else _FakeSlots()
    reader = reader if reader is not None else _reader(policy)
    relay = Relay(
        policy=policy, speak=fire.speak, slots=slots,
        occasion_for=_occasion_for, metrics=metrics, transport=reader.state,
    )
    return relay, fire, slots


async def _send(relay, **kw):
    base = {
        "room": "signal", "text": "we're starting", "requester": MILES,
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


async def test_a_room_in_another_unit_is_refused():
    """THE BOUNDARY. Rooms sharing a unit are one social unit reachable in
    more than one place, so the audience is already the same audience. Rooms in
    different units are different groups of people, and no amount of
    per-room trust makes speaking across them acceptable."""
    relay, fire, _ = _relay(_policy(other_unit=True))

    out = await _send(
        relay, room="discord", from_platform="signal",
        from_conversation_id=SIGNAL_ROOM,
    )

    assert out["status"] == "refused"
    assert fire.spoken == []


async def test_somebody_not_in_the_destination_cannot_speak_into_it():
    """The unit bounds WHICH rooms may be paired; this decides who may use
    the pair. Without it anyone in one room of a unit could speak into
    every other room of it — a promotion, not a relay.

    Ada is in the Discord room and NOT in the Signal one.
    """
    relay, fire, _ = _relay(_policy(ada_in_discord=True))

    out = await _send(relay, requester=ADA)

    assert out["status"] == "refused"
    assert fire.spoken == []


async def test_an_unknown_room_name_is_refused_not_guessed():
    relay, fire, _ = _relay(_policy())

    out = await _send(relay, room="telegram")

    assert out["status"] == "refused"
    assert fire.spoken == []


async def test_two_rooms_of_one_platform_are_ambiguous_rather_than_picked():
    """A network is an address only while it names exactly one room."""
    dupe = Conversation(
        platform="signal", ids=frozenset({OTHER_ROOM}), persons=frozenset({"miles_novak"}),
        label="Friends Overflow", primary_id=OTHER_ROOM, unit="friends",
    )
    base = _policy()
    rooms = (*base.conversations, dupe)
    policy = Policy(
        conversations=rooms,
        _by_id={(c.platform, cid): c for c in rooms for cid in c.ids},
    )
    relay, fire, _ = _relay(policy)

    out = await _send(relay)

    assert out["status"] == "ambiguous"
    assert out["rooms"] == ["Friends", "Friends Overflow"]
    assert "Ask which room" in out["note"]
    assert fire.spoken == []


async def test_relaying_into_this_very_room_is_refused():
    relay, fire, _ = _relay(_policy())

    out = await _send(relay, room="discord")

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
    relay = Relay(
        policy=_policy(), speak=None, slots=None, occasion_for=None,
        transport=_reader(_policy()).state,
    )

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


# ---------------------------------------------------------------------------
# a network that is not up is not tried
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("script", "says", "heals"),
    [
        (("degraded",), "Signal is down and needs a person to fix it", False),
        (("disconnected",), "Signal is reconnecting right now", True),
        (("raises",), "Signal's connection could not be checked", False),
        (("hangs",), "Signal's connection could not be checked", False),
        (None, "Signal is not running in this deployment", False),
    ],
    ids=["degraded", "disconnected", "health_raises", "health_hangs", "no_adapter"],
)
async def test_a_destination_that_is_not_up_is_never_tried(script, says, heals):
    """She must not call `send_to` into a network `<presence>` says is down. The
    guard is mechanical: the destination's transport is read through the SAME
    reader and mapping the block renders from, and anything but `up` answers
    `unavailable` in the block's own words without the fire path or the
    destination's slot ever being touched.

    A health read that raises or hangs is «could not be checked», and that
    refuses too: fail-closed, never read as `up`."""
    from nora.obs.metrics import Metrics

    metrics = Metrics()
    policy = _policy()
    signal = None if script is None else _Health("signal", *script)
    relay, fire, slots = _relay(
        policy, metrics=metrics, reader=_reader(policy, signal=signal),
    )

    out = await _send(relay)

    assert out["status"] == "unavailable"
    assert says in out["note"]
    assert "nothing was sent" in out["note"]
    # Only a transient state invites a retry. Offering «later» for a network
    # that needs a person, or that could not be checked, is a promise nothing
    # stands behind.
    assert ("comes back by itself" in out["note"]) is heals, out["note"]
    assert fire.spoken == [], "a network that is not up must never be spoken into"
    assert slots.acquired == [], "nor its room's slot taken"
    assert metrics.get("nora_relay_total", outcome="transport_down") == 1.0


async def test_the_refusal_leaks_no_id_and_no_adapter_detail():
    """`detail` is the operator's diagnostic and may name an account; room ids
    are identifiers (R-18). The note carries the network's display name and its
    state, and nothing the adapter or policy holds besides."""
    policy = _policy()
    for script in ("degraded", "raises"):
        relay, _f, _ = _relay(
            policy, reader=_reader(policy, signal=_Health("signal", script)),
        )

        out = await _send(relay)

        assert out["status"] == "unavailable"
        assert "nora_burner_account" not in str(out)
        assert "checkpointed" not in str(out)
        assert SIGNAL_ROOM not in str(out) and DISCORD_ROOM not in str(out)


async def test_health_is_read_on_every_relay_never_cached():
    """A network that was down on the last relay may be up on this one, and the
    reverse. One read per call, of that moment: a cached `up` is how she
    promises a send that then fails, and a cached `down` refuses a working
    one."""
    policy = _policy()
    signal = _Health("signal", "degraded", "up", "disconnected")
    relay, fire, _ = _relay(policy, reader=_reader(policy, signal=signal))

    first = await _send(relay)
    second = await _send(relay)
    third = await _send(relay)

    assert [first["status"], second["status"], third["status"]] == [
        "unavailable", "ok", "unavailable",
    ]
    assert signal.calls == 3
    assert len(fire.spoken) == 1


async def test_the_policy_gates_are_decided_before_the_network_is_read():
    """A person not allowed in the destination is told THAT, not how its
    network is doing: the gates are policy, and free, and the health read is
    I/O spent only on a relay that would otherwise go."""
    policy = _policy(ada_in_discord=True)
    signal = _Health("signal", "degraded")
    relay, fire, _ = _relay(policy, reader=_reader(policy, signal=signal))

    out = await _send(relay, requester=ADA)

    assert out["status"] == "refused"
    assert signal.calls == 0
    assert fire.spoken == []


@pytest.mark.parametrize(
    ("kwargs", "outcome"),
    [
        ({"requester": None}, "unmapped"),
        ({"room": "telegram"}, "unknown_room"),
        ({"room": "discord"}, "same_room"),
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


# ---------------------------------------------------------------------------
# ADR 0015: what to tell the asker rides in the result
# ---------------------------------------------------------------------------

_DASHES = ("—", "–")


async def test_every_non_ok_relay_outcome_carries_a_note():
    """`send_to`'s description names its statuses; what to tell the person
    here is each result's `note`. Every refusal and every unavailable path is
    driven, so one that loses its note goes red."""
    dupe = Conversation(
        platform="signal", ids=frozenset({OTHER_ROOM}), persons=frozenset({"miles_novak"}),
        label="Friends Overflow", primary_id=OTHER_ROOM, unit="friends",
    )
    rooms = (*_policy().conversations, dupe)
    doubled = Policy(conversations=rooms,
                     _by_id={(c.platform, cid): c for c in rooms for cid in c.ids})
    plain = _relay(_policy())[0]
    cases = {
        "refused/empty": (plain, {"text": "   "}),
        "refused/unmapped": (plain, {"requester": None}),
        "refused/unknown_source": (plain, {"from_conversation_id": "not-a-room"}),
        "refused/same_room": (plain, {"room": "discord"}),
        "refused/unknown_room": (plain, {"room": "telegram"}),
        "ambiguous/ambiguous_room": (_relay(doubled)[0], {}),
        "refused/not_allowed_there": (_relay(_policy(ada_in_discord=True))[0],
                                      {"requester": ADA}),
        "refused/egress": (plain, {"text": "call him on +85290000101"}),
        "unavailable/no_adapter": (
            Relay(policy=_policy(), speak=None, slots=None, occasion_for=None,
                  transport=_reader(_policy()).state), {}),
        "unavailable/transport_down": (
            _relay(_policy(), reader=_reader(
                _policy(), signal=_Health("signal", "degraded")))[0], {}),
        "unavailable/busy": (_relay(_policy(), slots=_FakeSlots(free=False))[0], {}),
        "unavailable/failed": (_relay(_policy(), fire=_FakeFire(outcome="failed_error"))[0],
                               {}),
    }
    for case, (relay, kw) in cases.items():
        out = await _send(relay, **kw)
        assert out["status"] == case.split("/")[0], (case, out)
        assert isinstance(out.get("note"), str) and out["note"].strip(), case
        assert not any(d in out["note"] for d in _DASHES), case


async def test_a_relay_that_went_tells_her_to_say_so_here():
    """The description's old «afterwards, tell the person here whether it went»
    rides with the `ok` it is about."""
    out = await _send(_relay(_policy())[0])
    assert out["status"] == "ok" and out["room"] == "Friends"
    assert "send_reply" in out["note"]


@pytest.mark.parametrize("address", ["Friends", "  fRiEnDs  ", " SIGNAL "])
async def test_room_names_fold_and_a_unique_network_still_resolves(address):
    relay, fire, _ = _relay(_policy())
    out = await _send(relay, room=address)
    assert out["status"] == "ok" and out["room"] == "Friends"
    assert fire.spoken == [("signal", SIGNAL_ROOM, "Miles: we're starting")]
    assert "Friends" in out["note"]


async def test_another_units_room_and_an_unknown_name_have_the_same_refusal():
    relay, fire, _ = _relay(_policy(other_unit=True))
    other = await _send(relay, room="Friends")
    unknown = await _send(relay, room="No such room")
    assert other == unknown
    assert other["status"] == "refused" and fire.spoken == []


async def test_two_rooms_on_one_network_send_between_them_but_not_to_itself():
    from dataclasses import replace

    base = _policy()
    first = base.conversations[0]
    dinner = replace(first, ids=frozenset({OTHER_ROOM}), primary_id=OTHER_ROOM,
                     label="Dinner Plans")
    rooms = (*base.conversations, dinner)
    policy = Policy(conversations=rooms,
                    _by_id={(c.platform, cid): c for c in rooms for cid in c.ids})
    relay, fire, slots = _relay(policy)
    out = await _send(relay, room="  dInNeR   pLaNs ", from_platform="signal",
                      from_conversation_id=SIGNAL_ROOM)
    assert out["status"] == "ok" and out["room"] == "Dinner Plans"
    assert fire.spoken == [("signal", OTHER_ROOM, "Miles: we're starting")]
    assert slots.acquired == slots.released == [("signal", OTHER_ROOM)]

    out = await _send(relay, room="Friends", from_platform="signal",
                      from_conversation_id=SIGNAL_ROOM)
    assert out["status"] == "refused"
    assert len(fire.spoken) == 1 and len(slots.acquired) == 1

    # Include the source among network candidates: two rooms still require a choice.
    out = await _send(relay, room="signal", from_platform="signal",
                      from_conversation_id=SIGNAL_ROOM)
    assert out["status"] == "ambiguous"
    assert out["rooms"] == ["Friends", "Dinner Plans"]
    assert len(fire.spoken) == 1


async def test_room_identity_includes_the_network_even_when_ids_match():
    from dataclasses import replace

    base = _policy()
    destination = replace(base.conversations[0], ids=frozenset({DISCORD_ROOM}),
                          primary_id=DISCORD_ROOM)
    rooms = (destination, base.conversations[1])
    policy = Policy(conversations=rooms,
                    _by_id={(c.platform, cid): c for c in rooms for cid in c.ids})
    relay, fire, _ = _relay(policy)
    out = await _send(relay, room="Friends")
    assert out["status"] == "ok"
    assert fire.spoken == [("signal", DISCORD_ROOM, "Miles: we're starting")]
