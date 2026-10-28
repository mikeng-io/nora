"""`ChannelDirectory` implementations for Discord.

`ClientChannelDirectory` reads discord.py's own cache through
`DiscordAdapter.current_client()`: the Gateway keeps `client.guilds`,
`guild.channels` and `guild.threads` current from GUILD_CREATE,
CHANNEL_CREATE/UPDATE/DELETE, THREAD_CREATE/UPDATE and the thread list sync,
which is exactly the discovery a server entry expands on — no extra event is
subscribed and no REST call is made on the read path. A fresh client per
listen attempt (`adapter.py`) means `current_client()` answers `None`
between attempts: every read then fails closed, `unavailable`, not a stale
half-answer.

`StaticChannelDirectory` is the same port over a fixed snapshot — what
`nora groups` builds from one REST read and what tests drive. It answers
`None` for guilds it was not given rather than claiming emptiness: unknown
is «could not look», an empty map is «looked, nothing there».

Both are duck-typed over the client (`get_guild`/`get_channel`, `guild.channels`,
`channel.type`/`name`/`parent_id`) so a test supplies the same shapes a
`discord.Client` carries — including a channel whose `.type` names no room
and a thread whose `.parent_id` names its room.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

# discord.py `ChannelType` values a server entry expands into: text,
# announcement and forum/media channels — the channels that can hold an
# ordinary message. Read as `.value` so a test can supply the bare int.
# Voice, stage, category and every thread type are excluded on purpose:
# voice is a SURFACE of a room (`Conversation.voice`), and a thread belongs
# to its parent channel's room, never one of its own.
_ROOM_CHANNEL_TYPES = frozenset({0, 5, 15, 16})


def _type_value(channel: Any) -> int | None:
    t = getattr(channel, "type", None)
    value = getattr(t, "value", t)
    return value if isinstance(value, int) else None


class ClientChannelDirectory:
    """The live `ChannelDirectory`, reading the connected client's cache."""

    def __init__(self, client_getter: Callable[[], Any]) -> None:
        self._client_getter = client_getter

    def guild_channels(self, *, guild_id: str) -> Mapping[str, str] | None:
        client = self._client_getter()
        if client is None:
            return None
        try:
            guild = client.get_guild(int(guild_id))
        except (TypeError, ValueError):
            return None
        if guild is None:
            return None
        return {
            str(ch.id): ch.name
            for ch in guild.channels
            if _type_value(ch) in _ROOM_CHANNEL_TYPES
            and getattr(ch, "name", None)
        }

    def parent_channel(self, *, channel_id: str) -> str | None:
        client = self._client_getter()
        if client is None:
            return None
        try:
            channel = client.get_channel(int(channel_id))
        except (TypeError, ValueError):
            return None
        if channel is None:
            return None
        parent = getattr(channel, "parent_id", None)
        return str(parent) if parent is not None else None


class StaticChannelDirectory:
    """A `ChannelDirectory` over a fixed snapshot.

    `channels` maps `guild_id -> {channel_id -> name}` (room-channels only —
    the CLI's REST read filters the same `_ROOM_CHANNEL_TYPES` before
    filling it). `threads` maps `thread_id -> parent channel_id` and may
    cover threads in guilds the snapshot knows nothing else about: a forum
    post's parent is still a fact, where the guild's channels are not.
    """

    def __init__(
        self,
        channels: Mapping[str, Mapping[str, str]] | None = None,
        threads: Mapping[str, str] | None = None,
    ) -> None:
        self._channels = channels or {}
        self._threads = threads or {}

    def guild_channels(self, *, guild_id: str) -> Mapping[str, str] | None:
        return self._channels.get(guild_id)

    def parent_channel(self, *, channel_id: str) -> str | None:
        return self._threads.get(channel_id)
