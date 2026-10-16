"""`/discord exit` applies the SAME three-condition admission gate as
`/discord join`: guild member + caller in a voice channel + that channel's
GUILD on `voice_guild_ids`, with no carve-out for exit. Without the gate any
guild member could tear down the shared connection (music + TTS + wake) from
anywhere. Every rejection path must leave the hub and the music manager
untouched.

`discord.Interaction` is not faithfully fakeable as a whole, but the gate
runs BEFORE any Discord API call and reads only `guild_id` / `user` /
`response` / `followup`, so a narrow stub is faithful for these paths.
`isinstance` checks are satisfied with `spec=`'d mocks.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord

from nora.platform.discord.discord_commands import DiscordCommands

GUILD = 111
CHANNEL = 222
ALLOWED = frozenset({str(GUILD)})


class _FakeResponse:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.deferred = False

    async def send_message(self, content: str, *, ephemeral: bool = False) -> None:
        self.messages.append(content)

    async def defer(self, *, ephemeral: bool = False) -> None:
        self.deferred = True


class _FakeFollowup:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, content: str, *, ephemeral: bool = False) -> None:
        self.messages.append(content)


class _FakeInteraction:
    def __init__(self, *, guild_id: int | None, user: object) -> None:
        self.guild_id = guild_id
        self.user = user
        self.response = _FakeResponse()
        self.followup = _FakeFollowup()


def _member(*, channel_id: int | None) -> MagicMock:
    member = MagicMock(spec=discord.Member)
    if channel_id is None:
        member.voice = None
    else:
        channel = MagicMock(spec=discord.VoiceChannel)
        channel.id = channel_id
        member.voice = MagicMock()
        member.voice.channel = channel
    return member


def _commands(
    allowed: frozenset[str] = ALLOWED,
) -> tuple[DiscordCommands, MagicMock, MagicMock]:
    hub = MagicMock()
    hub.exit = AsyncMock(return_value=True)
    manager = MagicMock()
    manager.leave_voice = AsyncMock()
    return (
        DiscordCommands(hub=hub, voice_guild_ids=allowed, manager=manager),
        hub,
        manager,
    )


async def _run_exit(commands: DiscordCommands, interaction: _FakeInteraction) -> None:
    await DiscordCommands.exit.callback(commands, interaction)


async def test_exit_rejects_a_member_in_no_voice_channel() -> None:
    commands, hub, manager = _commands()
    interaction = _FakeInteraction(guild_id=GUILD, user=_member(channel_id=None))
    await _run_exit(commands, interaction)
    hub.exit.assert_not_called()
    manager.leave_voice.assert_not_called()
    assert len(interaction.response.messages) == 1
    assert not interaction.response.deferred


async def test_exit_rejects_a_member_in_an_unallowed_guild() -> None:
    """The guild half of the gate. `voice_guild_ids` holds a DIFFERENT guild,
    so even a member sitting in a voice channel is refused."""
    commands, hub, manager = _commands(allowed=frozenset({"999"}))
    interaction = _FakeInteraction(guild_id=GUILD, user=_member(channel_id=CHANNEL))
    await _run_exit(commands, interaction)
    hub.exit.assert_not_called()
    manager.leave_voice.assert_not_called()
    assert len(interaction.response.messages) == 1


async def test_exit_admits_a_member_in_any_channel_of_an_allowed_guild() -> None:
    """A channel that is not the configured one is not a refusal: a member who
    moved rooms can tear down the connection they are sitting in."""
    commands, hub, _manager = _commands()
    interaction = _FakeInteraction(guild_id=GUILD, user=_member(channel_id=999))
    await _run_exit(commands, interaction)
    hub.exit.assert_awaited_once_with(str(GUILD))


async def test_exit_rejects_a_non_member_caller() -> None:
    commands, hub, manager = _commands()
    interaction = _FakeInteraction(
        guild_id=GUILD, user=MagicMock(spec=discord.User),
    )
    await _run_exit(commands, interaction)
    hub.exit.assert_not_called()
    manager.leave_voice.assert_not_called()


async def test_exit_rejects_a_dm() -> None:
    commands, hub, manager = _commands()
    interaction = _FakeInteraction(
        guild_id=None, user=_member(channel_id=CHANNEL),
    )
    await _run_exit(commands, interaction)
    hub.exit.assert_not_called()
    manager.leave_voice.assert_not_called()


async def test_exit_admits_a_member_in_the_allowlisted_channel() -> None:
    commands, hub, manager = _commands()
    interaction = _FakeInteraction(guild_id=GUILD, user=_member(channel_id=CHANNEL))
    await _run_exit(commands, interaction)
    manager.leave_voice.assert_awaited_once_with(str(GUILD))
    hub.exit.assert_awaited_once_with(str(GUILD))
    assert interaction.response.deferred
    assert len(interaction.followup.messages) == 1


async def test_exit_with_voice_disabled_still_gates_first() -> None:
    """Music-disabled / no-hub deployments reject at the gate, not with an
    AttributeError — the gate decision never touches hub or manager."""
    commands = DiscordCommands(hub=None, voice_guild_ids=ALLOWED, manager=None)
    interaction = _FakeInteraction(guild_id=GUILD, user=_member(channel_id=None))
    await _run_exit(commands, interaction)
    assert len(interaction.response.messages) == 1
    assert not interaction.response.deferred
