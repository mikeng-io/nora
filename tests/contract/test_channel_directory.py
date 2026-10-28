"""`ChannelDirectory` contract — every implementation answers the same
three facts about the same guild shape:

- `guild_channels` names the ROOM channels only (text, announcement,
  forum/media): never a voice/stage channel, never a thread.
- `parent_channel` names the channel a thread belongs to, `None` for an
  ordinary channel or an id the directory does not know.
- `None` means «could not look», never «looked, empty».
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from nora.domain.ports import ChannelDirectory
from nora.platform.discord.channels import (
    ClientChannelDirectory,
    StaticChannelDirectory,
)

GUILD = "100000000000000042"
TEXT = "100000000000000101"
ANNOUNCE = "100000000000000102"
FORUM = "100000000000000103"
VOICE = "100000000000000301"
THREAD = "100000000000000201"

# discord.py `ChannelType` wire values, as `guild.channels` reports them.
TYPE_TEXT, TYPE_VOICE, TYPE_FORUM, TYPE_THREAD = 0, 2, 15, 12


@dataclass
class _Channel:
    id: int
    name: str
    type: int
    parent_id: int | None = None


@dataclass
class _Guild:
    id: int
    channels: list[_Channel] = field(default_factory=list)


@dataclass
class _Client:
    """The slice of `discord.Client` the directory reads: `get_guild` and
    `get_channel` over the cached objects — a thread among them the way the
    gateway really reports one."""

    guilds: dict[int, _Guild] = field(default_factory=dict)

    def get_guild(self, guild_id: int) -> Any:
        return self.guilds.get(guild_id)

    def get_channel(self, channel_id: int) -> Any:
        for guild in self.guilds.values():
            for channel in guild.channels:
                if channel.id == channel_id:
                    return channel
        return None


def _stub_client() -> _Client:
    return _Client({
        int(GUILD): _Guild(
            id=int(GUILD),
            channels=[
                _Channel(int(TEXT), "general", TYPE_TEXT),
                _Channel(int(ANNOUNCE), "announcements", 5),
                _Channel(int(FORUM), "help-forum", TYPE_FORUM),
                _Channel(int(VOICE), "hangout", TYPE_VOICE),
                _Channel(int(THREAD), "a-post", TYPE_THREAD, parent_id=int(FORUM)),
            ],
        ),
    })


def _static() -> StaticChannelDirectory:
    return StaticChannelDirectory(
        {
            GUILD: {
                TEXT: "general", ANNOUNCE: "announcements", FORUM: "help-forum",
            },
        },
        threads={THREAD: FORUM},
    )


def _client_dir() -> ClientChannelDirectory:
    client = _stub_client()
    return ClientChannelDirectory(lambda: client)


@pytest.fixture(params=["client", "static"])
def directory(request) -> ChannelDirectory:
    return {"client": _client_dir, "static": _static}[request.param]()


class TestGuildChannels:
    def test_lists_only_room_channels_by_id(self, directory):
        channels = directory.guild_channels(guild_id=GUILD)

        assert channels == {
            TEXT: "general", ANNOUNCE: "announcements", FORUM: "help-forum",
        }

    def test_a_voice_channel_and_a_thread_are_not_rooms(self, directory):
        channels = directory.guild_channels(guild_id=GUILD)

        assert VOICE not in channels
        assert THREAD not in channels

    def test_an_unknown_guild_is_unavailable_not_empty(self, directory):
        assert directory.guild_channels(guild_id="999999999999999999") is None


class TestParentChannel:
    def test_a_thread_names_its_parent_channel(self, directory):
        assert directory.parent_channel(channel_id=THREAD) == FORUM

    def test_an_ordinary_channel_has_no_parent(self, directory):
        assert directory.parent_channel(channel_id=TEXT) is None

    def test_an_unknown_id_has_no_parent(self, directory):
        assert directory.parent_channel(channel_id="999999999999999999") is None


def test_a_disconnected_client_answers_unavailable_everywhere():
    """`current_client()` is `None` between listen attempts; every read
    must fail closed rather than answer from a stale object."""
    directory = ClientChannelDirectory(lambda: None)

    assert directory.guild_channels(guild_id=GUILD) is None
    assert directory.parent_channel(channel_id=TEXT) is None
