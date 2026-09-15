"""The `VoicePresenceSource` contract — one behavioural suite BOTH
implementations pass (`nora.platform.discord.voice_presence.
DiscordVoicePresence` against a stub live client, and
`nora.testing.FakeVoicePresenceSource`). The repo rule: a port never gains
a second implementation — test fakes included — without a shared contract
suite both pass.

The real side is driven against a stub adapter whose `current_client()`
hands over a guild cache built the way discord.py's own looks — guilds
with `get_member`, members with `.voice.channel` — with the channel a
`Mock(spec=discord.VoiceChannel)` so the implementation's own isinstance
check is what admits it. The port's promise, pinned here for both sides:

- a member in a voice channel answers a `VoiceChannelRef` KEYED BY THEIR
  DISCORD USER ID, and a member not in voice — or not a member at all —
  is simply ABSENT (their `nothing_found`);
- no live client, or the read itself failing, is `None`, never an
  exception and never `{}` — the tool's `unavailable` and a member's
  `nothing_found` never share a shape;
- EVERY guild the client can see is scanned (the question is room-scoped,
  not conversation-scoped), and a stage channel is not "in voice".
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import discord
import pytest

from nora.domain.ports import VoiceChannelRef, VoicePresenceSource
from nora.platform.discord.adapter import DiscordAdapter
from nora.platform.discord.voice_presence import DiscordVoicePresence
from nora.testing import FakeVoicePresenceSource


def _voice_channel(*, channel_id: int = 555, name: str = "general") -> Mock:
    channel = Mock(spec=discord.VoiceChannel)
    channel.id = channel_id
    channel.name = name
    return channel


class _Guild:
    """The slice of discord.py's `Guild` the implementation reads."""

    def __init__(self, members: dict[int, object]) -> None:
        self._members = members

    def get_member(self, member_id: int) -> object | None:
        return self._members.get(member_id)


def _member_in_voice(channel: object) -> SimpleNamespace:
    return SimpleNamespace(voice=SimpleNamespace(channel=channel))


def _scene() -> SimpleNamespace:
    """The stub live client: "42" in a voice channel in guild A, "43" a
    member of guild B not in voice, "46" in voice in guild B (proves the
    scan is every-guild), "45" on a STAGE channel (not voice)."""
    guild_a = _Guild({
        42: _member_in_voice(_voice_channel()),
        45: _member_in_voice(Mock(spec=discord.StageChannel)),
    })
    guild_b = _Guild({
        43: SimpleNamespace(voice=None),
        46: _member_in_voice(_voice_channel(channel_id=777, name="gaming")),
    })
    return SimpleNamespace(guilds=[guild_a, guild_b])


class _RealAdapter:
    """`DiscordVoicePresence` against a stub adapter/client."""

    async def start(self) -> None:
        self.client: object = _scene()
        adapter = SimpleNamespace(current_client=lambda: self.client)
        self.source = DiscordVoicePresence(adapter=adapter)  # type: ignore[arg-type]

    async def stop(self) -> None:
        return None

    def break_it(self) -> None:
        self.client = None  # no live connection right now


class _FakeAdapter:
    """`FakeVoicePresenceSource`, in the same harness shape as the real
    implementation so the port-level tests below read identically."""

    async def start(self) -> None:
        self.source = FakeVoicePresenceSource(channels={
            "42": VoiceChannelRef(channel_id="555", channel_name="general"),
            "46": VoiceChannelRef(channel_id="777", channel_name="gaming"),
        })

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
        DiscordVoicePresence(adapter=DiscordAdapter("tok")),
        FakeVoicePresenceSource(),
    ):
        assert isinstance(impl, VoicePresenceSource), type(impl).__name__


async def test_a_member_in_voice_answers_the_channel(harness) -> None:
    """The port's positive half, for every implementation: keyed by Discord
    user id, carrying the channel's id and name — and NOBODY else."""
    out = await harness.source.in_voice(member_ids=frozenset({"42", "43", "44"}))
    assert out is not None
    assert set(out) == {"42"}
    ref = out["42"]
    assert ref.channel_id == "555"
    assert ref.channel_name == "general"


async def test_a_member_not_in_voice_is_absent(harness) -> None:
    """A real member sitting out of voice is their `nothing_found` — an
    absent key, distinguishable from the read failing."""
    assert await harness.source.in_voice(member_ids=frozenset({"43"})) == {}


async def test_an_unknown_member_is_absent(harness) -> None:
    assert await harness.source.in_voice(member_ids=frozenset({"44"})) == {}


async def test_an_empty_request_answers_an_empty_dict(harness) -> None:
    assert await harness.source.in_voice(member_ids=frozenset()) == {}


async def test_a_failed_read_is_none_and_never_raises(harness) -> None:
    """Fail-closed by contract: no live client (the real side) or the
    fake's `fail` switch is `None` — the tool's `unavailable` — never an
    exception and never a confident "nobody is in voice"."""
    harness.break_it()
    assert await harness.source.in_voice(member_ids=frozenset({"42"})) is None


async def test_every_guild_is_scanned(real) -> None:
    """The question is room-scoped, not conversation-scoped: a member in
    voice in ANY guild the client can see answers, not just one guild's."""
    out = await real.source.in_voice(member_ids=frozenset({"46"}))
    assert out is not None
    assert out["46"].channel_id == "777"
    assert out["46"].channel_name == "gaming"


async def test_a_stage_channel_is_not_voice(real) -> None:
    assert await real.source.in_voice(member_ids=frozenset({"45"})) == {}


async def test_a_non_snowflake_id_is_skipped(real) -> None:
    """An id that is not a Discord snowflake at all is skipped, never a
    ValueError out of the scan."""
    assert await real.source.in_voice(member_ids=frozenset({"abc", "42"})) is not None


async def test_an_adapter_that_throws_is_none(real) -> None:
    """The never-raises backstop: anything discord.py throws mid-scan is
    the whole read unavailable, never an exception into the agent loop."""

    def _boom() -> object:
        raise RuntimeError("gateway gone")

    adapter = SimpleNamespace(current_client=_boom)
    source = DiscordVoicePresence(adapter=adapter)  # type: ignore[arg-type]
    assert await source.in_voice(member_ids=frozenset({"42"})) is None
