"""`online_now` — the live digital-activity tool and its fail-closed matrix.

The shape mirrors `tests/test_where_been.py`: the tool is driven through
`build_tool_specs` — the same wiring the tag path uses — against the three
contracted fakes (`nora.testing.FakeSteamPresenceProvider` /
`FakeVoicePresenceSource` / `FakeDiscordPresenceSource`), with the room
resolved through `FakeRosterSource`. The pinned behaviours are the honesty rules:
away/snooze are never flattened, `profile_hidden` is never "not playing",
an unwired or failed read is `unavailable` never `nothing_found`, a
degraded room is `unavailable` never a one-person answer, and a names
filter narrows the input domain but never widens it past the room.
"""

from __future__ import annotations

import pytest

from nora.agent.room import Room
from nora.agent.tools import build_tool_specs
from nora.domain.ports import DiscordPresenceState, SteamPlayerState, VoiceChannelRef
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.testing import (
    FakeDiscordPresenceSource,
    FakeRosterSource,
    FakeSteamPresenceProvider,
    FakeVoicePresenceSource,
)

GROUP = "Lf2L9zqeNUahRsOz6BA+sTB8aWfbuvqu9vOhpvmJi34="
NORA = frozenset({"+85290000100"})
MILES = frozenset({"+85290000101"})
BLAKE = frozenset({"+85260000001"})
ANN = frozenset({"+85260000002"})
OUTSIDER = frozenset({"+85260000000"})

IDENTITY = """
[persons.miles_novak]
first_name = "Miles"
last_name  = "Novak"
aliases = ["Miles"]
[[persons.miles_novak.signal]]
phone = "+85290000101"
[[persons.miles_novak.pulse]]
steam_id = "111"
[[persons.miles_novak.discord]]
user_id = "1001"

[persons.blake_lindqvist]
first_name = "Blake"
last_name  = "Lindqvist"
aliases = ["Blake"]
[[persons.blake_lindqvist.signal]]
phone = "+85260000001"
[[persons.blake_lindqvist.pulse]]
steam_id = "222"
[[persons.blake_lindqvist.discord]]
user_id = "1002"

[persons.ann_wong]
first_name = "Ann"
last_name  = "Walsh"
[[persons.ann_wong.signal]]
phone = "+85260000002"

[persons.outsider]
first_name = "Sam"
last_name  = "Outsider"
[[persons.outsider.signal]]
phone = "+85260000000"
[[persons.outsider.pulse]]
steam_id = "333"
"""


@pytest.fixture
def persons(tmp_path):
    path = tmp_path / "identity.toml"
    path.write_text(IDENTITY)
    return load_persons(path)


def _room(persons, *, members, fail=False, tagger=None, roster=None):
    return Room(
        platform="signal", conversation_id=GROUP,
        roster_source=roster or FakeRosterSource(groups={GROUP: tuple(members)}, fail=fail),
        persons=persons, self_ids=NORA, tagger=tagger,
    )


def _online_now(
    persons, *, members=(MILES, BLAKE), online_source=None, voice_source=None,
    presence=None, fail=False, tagger=None, metrics=None,
    roster=None,
):
    """The built `online_now` closure, through `build_tool_specs` — the same
    wiring the tag path uses (registry -> specs -> call), with the three
    sources threaded exactly as the composition root threads them."""
    specs = build_tool_specs(
        store=None, conversation_id=GROUP, conversation_type="group",
        persons=persons, honcho=None, deliverer=None,
        metrics=metrics if metrics is not None else Metrics(),
        room=_room(persons, members=members, fail=fail, tagger=tagger, roster=roster),
        transit_source=None,
        online_source=online_source, voice_source=voice_source,
        presence=presence,
    )
    return next(s for s in specs if s.name == "online_now").call


def _by_person(out):
    return {entry["person"]: entry for entry in out["people"]}


def _labelled(metrics, name, label_key, label_value):
    """`Metrics.snapshot()` keys samples `(name, sorted label pairs)`; this
    says whether one sample of `name` carries the given label value."""
    return any(
        n == name and (label_key, label_value) in labels
        for (n, labels), _value in metrics.snapshot().items()
    )


MILES_ONLINE = SteamPlayerState(
    steam_id="111", personastate=1, profile_public=True,
    game="Counter-Strike 2", game_id="730",
)
BLAKE_AWAY = SteamPlayerState(steam_id="222", personastate=3, profile_public=True)
BLAKE_HIDDEN = SteamPlayerState(steam_id="222", personastate=1, profile_public=False)
OUTSIDER_ONLINE = SteamPlayerState(steam_id="333", personastate=1, profile_public=True)


# ---- the inert/unavailable half — a wiring gap is never a confident empty ----


async def test_inert_default_every_arm_is_unavailable(persons):
    """Ships INERT: no `[online]`, no Discord — every arm answers
    `unavailable` (R-5), NEVER `nothing_found` («everyone is offline»)."""
    tool = _online_now(persons)
    for arm in ("online", "playing", "voice"):
        out = await tool(arm)
        assert out["status"] == "unavailable", arm
        assert "note" in out


async def test_an_unknown_what_is_one_unavailable(persons):
    """The closed `what` vocabulary: anything else is ONE `unavailable`,
    fail-closed — never a silent `{}` and never a guessed arm."""
    steam = FakeSteamPresenceProvider(players={"111": MILES_ONLINE})
    tool = _online_now(persons, online_source=steam)
    out = await tool("gaming")
    assert out["status"] == "unavailable"
    assert steam.calls == []  # no read behind a refused arm


async def test_a_failed_steam_read_is_unavailable(persons):
    """Steam down is «I couldn't check», never «everyone is offline» and
    never an exception into the loop."""
    steam = FakeSteamPresenceProvider(fail=True)
    tool = _online_now(persons, online_source=steam)
    for arm in ("online", "playing"):
        out = await tool(arm)
        assert out["status"] == "unavailable", arm


async def test_a_degraded_room_is_unavailable_not_a_one_person_answer(persons):
    """The proactive trap: `Room.scope` narrowing to the tagger alone
    (`degraded=True`) must NOT fall through to a roster-of-one answer —
    an ERROR is never a policy answer (the where_been discipline)."""
    steam = FakeSteamPresenceProvider(players={"111": MILES_ONLINE})
    tagger = persons.resolve("signal", "+85290000101")
    tool = _online_now(persons, online_source=steam, fail=True, tagger=tagger)
    out = await tool("online")
    assert out["status"] == "unavailable"


async def test_an_unreadable_room_is_unavailable(persons):
    steam = FakeSteamPresenceProvider(players={"111": MILES_ONLINE})
    tool = _online_now(persons, online_source=steam, fail=True)
    assert (await tool("online"))["status"] == "unavailable"


# ---- the names filter — narrows the domain, never widens it ----


async def test_names_filter_drops_outsiders(persons):
    """An outsider's name is filtered OUT of the input domain — never a
    look-up of a stranger, and their id never reaches Steam."""
    steam = FakeSteamPresenceProvider(players={
        "111": MILES_ONLINE, "222": BLAKE_AWAY, "333": OUTSIDER_ONLINE,
    })
    tool = _online_now(persons, online_source=steam)
    out = await tool("online", names=["Miles", "Sam"])
    assert out["status"] == "ok"
    assert set(_by_person(out)) == {"Miles Novak"}
    # ONE batched call, and the outsider's id was never in it.
    assert steam.calls == [frozenset({"111"})]


async def test_names_matching_nobody_in_scope_is_nothing_found(persons):
    steam = FakeSteamPresenceProvider(players={"333": OUTSIDER_ONLINE})
    metrics = Metrics()
    tool = _online_now(persons, online_source=steam, metrics=metrics)
    out = await tool("online", names=["Sam"])
    assert out["status"] == "nothing_found"
    # ...and it is COUNTED as the tool's own nothing_found.
    assert _labelled(metrics, "nora_tool_calls_total", "status", "nothing_found")


# ---- the honesty rules — mutation-check targets ----


async def test_away_and_snooze_are_reported_as_such(persons):
    """personastate honesty: away is `away`, never flattened to online
    — and never to offline either."""
    steam = FakeSteamPresenceProvider(players={
        "111": SteamPlayerState(steam_id="111", personastate=4, profile_public=True),
        "222": BLAKE_AWAY,
    })
    tool = _online_now(persons, online_source=steam)
    out = await tool("online")
    assert out["status"] == "ok"
    people = _by_person(out)
    assert people["Miles Novak"]["state"] == "snooze"
    assert people["Blake Lindqvist"]["state"] == "away"


async def test_profile_hidden_is_never_not_playing(persons):
    """A private profile withholds `gameextrainfo`: the entry says
    `profile_hidden` and asserts NOTHING about play state — hidden is not
    "not playing", and it is not "offline" either."""
    steam = FakeSteamPresenceProvider(players={
        "111": MILES_ONLINE, "222": BLAKE_HIDDEN,
    })
    tool = _online_now(persons, online_source=steam)
    out = await tool("playing")
    assert out["status"] == "ok"
    blake = _by_person(out)["Blake Lindqvist"]
    assert blake["state"] == "online"  # the state itself still shows
    assert blake["profile_hidden"] is True
    assert "playing" not in blake
    assert "game" not in blake


async def test_playing_reports_the_title_and_the_absent_one_honestly(persons):
    """The in-game arm: in-game carries the title (and id); a PUBLIC profile
    with no game is `playing: False` — an assertion Steam's answer ground."""
    steam = FakeSteamPresenceProvider(players={
        "111": MILES_ONLINE, "222": BLAKE_AWAY,
    })
    tool = _online_now(persons, online_source=steam)
    out = await tool("playing")
    assert out["status"] == "ok"
    people = _by_person(out)
    miles = people["Miles Novak"]
    assert miles["game"] == "Counter-Strike 2"
    assert miles["game_id"] == "730"
    blake = people["Blake Lindqvist"]
    assert blake["state"] == "away"
    assert blake["playing"] is False
    # R-18: no steam id or discord id ever leaves the tool.
    assert "111" not in str(out) and "1001" not in str(out)


async def test_no_pulse_block_is_nothing_found_for_that_person(persons):
    """A person with no `pulse` block has no steam_id: alone they are
    the arm's `nothing_found`; beside a readable person they are an honest
    `unknown`, never "offline"."""
    steam = FakeSteamPresenceProvider(players={"111": MILES_ONLINE})
    alone = _online_now(persons, members=(ANN,), online_source=steam)
    assert (await alone("playing"))["status"] == "nothing_found"

    mixed = _online_now(persons, members=(MILES, ANN), online_source=steam)
    out = await mixed("playing")
    assert out["status"] == "ok"
    ann = _by_person(out)["Ann Walsh"]
    assert ann["state"] == "unknown"
    assert ann["reason"] == "no_steam_id"


async def test_one_question_is_one_batched_steam_call(persons):
    """The budget's own sizing: the room's whole id set goes out in ONE
    GetPlayerSummaries request, never one per person."""
    steam = FakeSteamPresenceProvider(players={
        "111": MILES_ONLINE, "222": BLAKE_AWAY,
    })
    tool = _online_now(persons, online_source=steam)
    await tool("online")
    assert steam.calls == [frozenset({"111", "222"})]


# ---- the voice arm ----


async def test_voice_arm_reports_channel_membership(persons):
    voice = FakeVoicePresenceSource(channels={
        "1002": VoiceChannelRef(channel_id="555", channel_name="general"),
    })
    tool = _online_now(persons, voice_source=voice)
    out = await tool("voice")
    assert out["status"] == "ok"
    people = _by_person(out)
    assert people["Blake Lindqvist"]["in_voice"] == {
        "channel_id": "555", "channel_name": "general",
    }
    assert people["Miles Novak"]["in_voice"] is False


async def test_voice_arm_no_discord_id_is_unknown_not_absent(persons):
    voice = FakeVoicePresenceSource()
    tool = _online_now(persons, members=(MILES, ANN), voice_source=voice)
    out = await tool("voice")
    assert out["status"] == "ok"
    ann = _by_person(out)["Ann Walsh"]
    assert ann["state"] == "unknown"
    assert ann["reason"] == "no_discord_id"


async def test_voice_arm_unwired_and_failed_are_unavailable(persons):
    assert (await _online_now(persons)("voice"))["status"] == "unavailable"
    failing = _online_now(persons, voice_source=FakeVoicePresenceSource(fail=True))
    assert (await failing("voice"))["status"] == "unavailable"


# ---- the merged online arm ----


async def test_online_arm_merges_steam_and_discord_presence(persons):
    """The merged arm reads per-person Discord PRESENCE — status + rich
    presence — with voice membership riding along as one more fact, never the
    definition of "online"."""
    steam = FakeSteamPresenceProvider(players={
        "111": MILES_ONLINE, "222": BLAKE_AWAY,
    })
    presence = FakeDiscordPresenceSource(states={
        "1001": DiscordPresenceState(status="online", game="Elden Ring"),
        "1002": DiscordPresenceState(status="idle", listening="Spotify"),
    })
    voice = FakeVoicePresenceSource(channels={
        "1002": VoiceChannelRef(channel_id="555", channel_name="general"),
    })
    tool = _online_now(
        persons, online_source=steam, presence=presence, voice_source=voice,
    )
    out = await tool("online")
    assert out["status"] == "ok"
    assert out["steam_wired"] is True
    assert out["discord_wired"] is True
    assert out["voice_wired"] is True
    people = _by_person(out)
    miles = people["Miles Novak"]
    assert miles["state"] == "online"
    assert miles["game"] == "Counter-Strike 2"
    assert miles["discord_status"] == "online"
    assert miles["discord_game"] == "Elden Ring"
    blake = people["Blake Lindqvist"]
    assert blake["state"] == "away"
    assert blake["discord_status"] == "idle"
    assert blake["discord_listening"] == "Spotify"
    assert blake["in_voice"] == {"channel_id": "555", "channel_name": "general"}


async def test_online_arm_forum_typing_member_is_online(persons):
    """A member typing in a forum thread is ONLINE on Discord and in no voice
    channel; reading `member.voice` would report 「冇人上線」. The arm answers
    from presence: `discord_status` online, no rich presence, `in_voice`
    False as an extra fact."""
    presence = FakeDiscordPresenceSource(states={
        "1002": DiscordPresenceState(status="online"),
    })
    voice = FakeVoicePresenceSource()  # checked; Blake is in no channel
    tool = _online_now(persons, presence=presence, voice_source=voice)
    out = await tool("online", names=["Blake"])
    assert out["status"] == "ok"
    blake = _by_person(out)["Blake Lindqvist"]
    assert blake["discord_status"] == "online"
    assert "discord_game" not in blake
    assert "discord_listening" not in blake
    assert blake["in_voice"] is False
    assert "state" not in blake  # Steam was never wired — no guessed word


async def test_online_arm_presence_read_failure_degrades_not_sinks(persons):
    """A failed presence read in the merged arm flips `discord_wired` to
    False — the Steam half still stands, and the flag says the Discord
    half could not be checked. It is NOT a whole-arm `unavailable`."""
    steam = FakeSteamPresenceProvider(players={"111": MILES_ONLINE, "222": BLAKE_AWAY})
    presence = FakeDiscordPresenceSource(fail=True)
    tool = _online_now(persons, online_source=steam, presence=presence)
    out = await tool("online")
    assert out["status"] == "ok"
    assert out["steam_wired"] is True
    assert out["discord_wired"] is False
    miles = _by_person(out)["Miles Novak"]
    assert miles["state"] == "online"
    assert "discord_status" not in miles


async def test_online_arm_no_presence_source_is_discord_unwired(persons):
    """No presence source wired (Discord disabled): the arm answers from
    the wired halves and says the Discord presence half could not be
    checked — never a guessed `discord_status`."""
    steam = FakeSteamPresenceProvider(players={"111": MILES_ONLINE, "222": BLAKE_AWAY})
    tool = _online_now(persons, online_source=steam)
    out = await tool("online")
    assert out["status"] == "ok"
    assert out["discord_wired"] is False
    assert "discord_status" not in _by_person(out)["Miles Novak"]


async def test_online_arm_voice_read_failure_degrades_not_sinks(persons):
    """A failed voice read in the merged arm flips `voice_wired` to False —
    the Steam and presence halves still stand, and the flag says the voice
    half could not be checked. It is NOT a whole-arm `unavailable`."""
    steam = FakeSteamPresenceProvider(players={"111": MILES_ONLINE, "222": BLAKE_AWAY})
    presence = FakeDiscordPresenceSource(states={
        "1001": DiscordPresenceState(status="online"),
    })
    voice = FakeVoicePresenceSource(fail=True)
    tool = _online_now(
        persons, online_source=steam, presence=presence, voice_source=voice,
    )
    out = await tool("online")
    assert out["status"] == "ok"
    assert out["steam_wired"] is True
    assert out["discord_wired"] is True
    assert out["voice_wired"] is False
    miles = _by_person(out)["Miles Novak"]
    assert miles["state"] == "online"
    assert miles["discord_status"] == "online"
    assert "in_voice" not in miles


async def test_online_arm_discord_only_composition(persons):
    """No `[online]` key but Discord wired: the arm answers from the
    presence half alone and says the Steam half could not be checked."""
    presence = FakeDiscordPresenceSource(states={
        "1001": DiscordPresenceState(status="online"),
        "1002": DiscordPresenceState(status="dnd", game="Balatro"),
    })
    tool = _online_now(persons, presence=presence)
    out = await tool("online")
    assert out["status"] == "ok"
    assert out["steam_wired"] is False
    assert out["discord_wired"] is True
    people = _by_person(out)
    assert people["Miles Novak"]["discord_status"] == "online"
    blake = people["Blake Lindqvist"]
    assert blake["discord_status"] == "dnd"
    assert blake["discord_game"] == "Balatro"


async def test_online_arm_voice_only_composition(persons):
    """Voice wired but neither Steam nor presence: the arm still answers
    from the voice half alone (one more Discord fact), and says the other
    halves could not be checked."""
    voice = FakeVoicePresenceSource(channels={
        "1001": VoiceChannelRef(channel_id="555", channel_name="general"),
    })
    tool = _online_now(persons, voice_source=voice)
    out = await tool("online")
    assert out["status"] == "ok"
    assert out["steam_wired"] is False
    assert out["discord_wired"] is False
    assert out["voice_wired"] is True
    people = _by_person(out)
    assert people["Miles Novak"]["in_voice"] == {
        "channel_id": "555", "channel_name": "general",
    }
    assert people["Blake Lindqvist"]["in_voice"] is False


# ---- ADR 0015 §3: how to report a status rides in the result ----


async def test_every_non_ok_status_carries_a_note(persons):
    """ADR 0015 §3/§6: the description only NAMES the statuses; what to say
    for each is the result's `note`. Every non-ok code path is driven here, so
    a path that loses its note (the reporting rule for that status) goes red."""
    steam = FakeSteamPresenceProvider(players={"111": MILES_ONLINE})
    tagger = persons.resolve("signal", "+85290000101")

    cases = {
        "unavailable/what": (_online_now(persons, online_source=steam), ("gaming",), {}),
        "unavailable/unwired": (_online_now(persons), ("playing",), {}),
        "unavailable/room": (
            _online_now(persons, online_source=steam, fail=True, tagger=tagger),
            ("online",), {},
        ),
        "unavailable/steam": (
            _online_now(persons, online_source=FakeSteamPresenceProvider(fail=True)),
            ("playing",), {},
        ),
        "unavailable/voice": (
            _online_now(persons, voice_source=FakeVoicePresenceSource(fail=True)),
            ("voice",), {},
        ),
        "nothing_found/names": (
            _online_now(persons, online_source=steam), ("online",), {"names": ["Sam"]},
        ),
        "nothing_found/unreadable": (
            _online_now(persons, members=(MILES, ANN), online_source=steam),
            ("playing",), {"names": ["Ann"]},
        ),
    }
    for case, (tool, args, kwargs) in cases.items():
        out = await tool(*args, **kwargs)
        assert out["status"] == case.split("/")[0], (case, out)
        assert isinstance(out.get("note"), str) and out["note"].strip(), case
        assert "—" not in out["note"] and "–" not in out["note"], case


async def test_an_ok_note_carries_the_rules_its_answer_needs(persons):
    """The honesty rules that left the description (away/snooze are answers,
    profile_hidden is not "not playing", an unwired half is not the whole
    picture) arrive in the `ok` they are about, and only when their field is."""
    plain = await _online_now(
        persons, online_source=FakeSteamPresenceProvider(players={"111": MILES_ONLINE}),
    )("playing", names=["Miles"])
    assert plain["status"] == "ok"
    assert "right now" in plain["note"]
    for clause in ("away", "profile_hidden", "unknown"):
        assert clause not in plain["note"], clause

    mixed = await _online_now(
        persons, online_source=FakeSteamPresenceProvider(players={"222": BLAKE_AWAY}),
    )("online")
    assert "away and snooze" in mixed["note"]
    assert "state unknown" in mixed["note"]      # Miles: Steam answered nothing
    assert "_wired" in mixed["note"]             # no Discord, no voice wired

    hidden = await _online_now(
        persons, online_source=FakeSteamPresenceProvider(players={"222": BLAKE_HIDDEN}),
    )("playing", names=["Blake"])
    assert "profile_hidden" in hidden["note"]
