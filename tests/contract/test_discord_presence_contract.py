"""The `DiscordPresenceSource` contract — one behavioural suite BOTH
implementations pass (`nora.platform.discord.discord_presence.
DiscordPresence` against a stub live client, and
`nora.testing.FakeDiscordPresenceSource`). The repo rule: a port never
gains a second implementation — test fakes included — without a shared
contract suite both pass.

The real side is driven against a stub adapter whose `current_client()`
hands over a guild cache built the way discord.py's own looks — guilds
with `get_member`, members with `.status` and `.activities`, the
activities real discord.py objects so the implementation's own isinstance
checks are what admits them. The port's promise, pinned here for both
sides (per-person presence, NOT voice membership, is what "Discord online"
means):

- a member the client can see answers a `DiscordPresenceState` KEYED BY
  THEIR DISCORD USER ID — Discord's own status word, plus rich presence
  only when an activity names it — and a member the client cannot see is
  simply ABSENT (their `nothing_found`);
- no live client, or the read itself failing, is `None`, never an
  exception and never `{}` — the tool's `unavailable` and a member's
  `nothing_found` never share a shape;
- EVERY guild the client can see is scanned (the question is room-scoped,
  not conversation-scoped).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import discord
import pytest

from nora.domain.ports import DiscordPresenceSource, DiscordPresenceState
from nora.platform.discord.adapter import DiscordAdapter
from nora.platform.discord.discord_presence import DiscordPresence
from nora.testing import FakeDiscordPresenceSource


class _Guild:
    """The slice of discord.py's `Guild` the implementation reads."""

    def __init__(self, members: dict[int, object]) -> None:
        self._members = members

    def get_member(self, member_id: int) -> object | None:
        return self._members.get(member_id)


def _member(status: object, activities: tuple = ()) -> SimpleNamespace:
    return SimpleNamespace(status=status, activities=activities)


def _scene() -> SimpleNamespace:
    """The stub live client: "42" online playing a game in guild A, "43"
    idle listening to music in guild B (proves the scan is every-guild),
    "45" dnd with a custom status, "46" offline with no activities."""
    guild_a = _Guild({
        42: _member(discord.Status.online, (discord.Game(name="Counter-Strike 2"),)),
        45: _member(discord.Status.dnd, (discord.CustomActivity(name="vibing"),)),
    })
    guild_b = _Guild({
        43: _member(
            discord.Status.idle,
            (discord.Activity(type=discord.ActivityType.listening, name="Spotify"),),
        ),
        46: _member(discord.Status.offline),
    })
    return SimpleNamespace(guilds=[guild_a, guild_b])


_FAKE_STATES = {
    "42": DiscordPresenceState(status="online", game="Counter-Strike 2"),
    "43": DiscordPresenceState(status="idle", listening="Spotify"),
    "45": DiscordPresenceState(status="dnd", custom_status="vibing"),
    "46": DiscordPresenceState(status="offline"),
}


class _RealAdapter:
    """`DiscordPresence` against a stub adapter/client."""

    async def start(self) -> None:
        self.client: object = _scene()
        adapter = SimpleNamespace(current_client=lambda: self.client)
        self.source = DiscordPresence(adapter=adapter)  # type: ignore[arg-type]

    async def stop(self) -> None:
        return None

    def break_it(self) -> None:
        self.client = None  # no live connection right now


class _FakeAdapter:
    """`FakeDiscordPresenceSource`, in the same harness shape as the real
    implementation so the port-level tests below read identically."""

    async def start(self) -> None:
        self.source = FakeDiscordPresenceSource(states=_FAKE_STATES)

    async def stop(self) -> None:
        return None

    def break_it(self) -> None:
        self.source.fail = True


_ADAPTERS = {"real": _RealAdapter, "fake": _FakeAdapter}


@pytest.fixture(params=["real", "fake"])
async def harness(request):
    """Every implementation of the PORT, the shared fake included."""
    adapter = _ADAPTERS[request.param]()
    await adapter.start()
    yield adapter
    await adapter.stop()


@pytest.fixture
async def real():
    adapter = _RealAdapter()
    await adapter.start()
    yield adapter
    await adapter.stop()


def test_every_implementation_satisfies_the_port() -> None:
    for impl in (
        DiscordPresence(adapter=DiscordAdapter("tok")),
        FakeDiscordPresenceSource(),
    ):
        assert isinstance(impl, DiscordPresenceSource), type(impl).__name__


async def test_a_visible_member_answers_their_state(harness) -> None:
    """The port's positive half, for every implementation: keyed by Discord
    user id, carrying Discord's own status word — and NOBODY else."""
    out = await harness.source.presence(member_ids=frozenset({"42", "43", "44"}))
    assert out is not None
    assert set(out) == {"42", "43"}
    assert out["42"].status == "online"
    assert out["43"].status == "idle"


async def test_an_offline_member_is_an_honest_answer(harness) -> None:
    """`offline` is a CHECKED state, never a guess: the member was read and
    Discord says offline — distinguishable from an absent key."""
    out = await harness.source.presence(member_ids=frozenset({"46"}))
    assert out is not None
    assert out["46"].status == "offline"


async def test_an_unknown_member_is_absent(harness) -> None:
    assert await harness.source.presence(member_ids=frozenset({"44"})) == {}


async def test_an_empty_request_answers_an_empty_dict(harness) -> None:
    assert await harness.source.presence(member_ids=frozenset()) == {}


async def test_a_failed_read_is_none_and_never_raises(harness) -> None:
    """Fail-closed by contract: no live client (the real side) or the
    fake's `fail` switch is `None` — the tool's `unavailable` — never an
    exception and never a confident "everyone is offline"."""
    harness.break_it()
    assert await harness.source.presence(member_ids=frozenset({"42"})) is None


async def test_every_guild_is_scanned(real) -> None:
    """The question is room-scoped, not conversation-scoped: a member in
    ANY guild the client can see answers, not just one guild's."""
    out = await real.source.presence(member_ids=frozenset({"43"}))
    assert out is not None
    assert out["43"].status == "idle"


async def test_rich_presence_names_only_what_the_activity_names(real) -> None:
    """A Game activity carries its title; a listening activity its name; a
    custom status its text. Fields no activity named stay None — present-
    optional, never a guessed word."""
    out = await real.source.presence(member_ids=frozenset({"42", "43", "45", "46"}))
    assert out is not None
    gamer = out["42"]
    assert gamer.game == "Counter-Strike 2"
    assert gamer.listening is None
    listener = out["43"]
    assert listener.listening == "Spotify"
    assert listener.game is None
    custom = out["45"]
    assert custom.status == "dnd"
    assert custom.custom_status == "vibing"
    quiet = out["46"]
    assert quiet.game is None
    assert quiet.listening is None
    assert quiet.custom_status is None


async def test_a_non_snowflake_id_is_skipped(real) -> None:
    """An id that is not a Discord snowflake at all is skipped, never a
    ValueError out of the scan."""
    assert await real.source.presence(member_ids=frozenset({"abc", "42"})) is not None


async def test_an_adapter_that_throws_is_none(real) -> None:
    """The never-raises backstop: anything discord.py throws mid-scan is
    the whole read unavailable, never an exception into the agent loop."""

    def _boom() -> object:
        raise RuntimeError("gateway gone")

    adapter = SimpleNamespace(current_client=_boom)
    source = DiscordPresence(adapter=adapter)  # type: ignore[arg-type]
    assert await source.presence(member_ids=frozenset({"42"})) is None


async def test_a_member_object_without_activities_still_answers(real) -> None:
    """A member whose activities are None (an odd cache state) is still a
    checked status answer, never a scan failure."""
    guild = _Guild({47: SimpleNamespace(status=discord.Status.online, activities=None)})
    adapter = SimpleNamespace(
        current_client=lambda: SimpleNamespace(guilds=[guild]),
    )
    source = DiscordPresence(adapter=adapter)  # type: ignore[arg-type]
    out = await source.presence(member_ids=frozenset({"47"}))
    assert out is not None
    assert out["47"].status == "online"
    assert out["47"].game is None


def test_mock_spotify_activity_is_listening() -> None:
    """The Spotify branch: a `discord.Spotify`-shaped activity contributes
    its track title as `listening` (constructed as a spec'd Mock — a real
    Spotify object needs gateway data nothing here has)."""
    from nora.platform.discord.discord_presence import _rich_presence

    track = Mock(spec=discord.Spotify)
    track.title = "Treasure"
    assert _rich_presence((track,))["listening"] == "Treasure"
