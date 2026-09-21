"""`music_notify.make_notify` — WHERE a music announcement lands
(design/discord-music/10-notification-routing.md).

**Unit-testable where `music_commands.py` is not**, and for the reason that
module's docstring gives in reverse: it touches no `discord.Interaction`, no
`Member.voice`, no `VoiceChannel.connect()`. Its entire Discord surface is
`Client.get_channel`, an `isinstance` against `discord.abc.Messageable`, and
`send`. The fake below subclasses the REAL `Messageable` rather than
duck-typing past the check, so the isinstance gate under test is the
production one (CLAUDE.md's fake-realism rule: the cheap fake here would be
an object that merely has a `send`, which would pass a check production
would fail).
"""

from __future__ import annotations

import discord
import pytest

from nora.platform.discord.music_notify import make_notify, no_music_room

GUILD = "100000000000000203"
STARTED_IN = "100000000000000204"
MUSIC_ROOM = "1785459208793411409"


class _Channel(discord.abc.Messageable):
    """A real `Messageable` whose `send` records instead of posting."""

    def __init__(self, channel_id: str) -> None:
        self.id = int(channel_id)
        self.sent: list[tuple[str, bool]] = []

    async def _get_channel(self):  # the one abstract member
        return self

    async def send(self, content=None, **kwargs):  # type: ignore[override]
        self.sent.append((content, bool(kwargs.get("silent"))))
        return None


class _Client:
    """`get_channel` over a fixed id → channel map; anything else is absent,
    which is what a deleted channel or one the bot lost access to looks
    like."""

    def __init__(self, *channels: _Channel) -> None:
        self._by_id = {c.id: c for c in channels}
        self.lookups: list[int] = []

    def get_channel(self, channel_id: int):
        self.lookups.append(channel_id)
        return self._by_id.get(channel_id)


def _notify(client, *, routes_to: str | None = None, fallback: str | None = STARTED_IN):
    return make_notify(
        client=lambda: client,
        guild_id=GUILD,
        fallback_channel_id=fallback,
        resolve_music_channel=(
            (lambda _g: routes_to) if routes_to is not None else no_music_room
        ),
    )


async def test_with_no_music_room_it_announces_where_the_session_started():
    """The pre-flag behaviour, kept exactly: a deployment that never sets
    `music = true` must be byte-identical to before this module existed."""
    started = _Channel(STARTED_IN)
    client = _Client(started)

    await _notify(client)("Now playing: x — u")

    assert started.sent == [("Now playing: x — u", True)]


async def test_a_music_room_takes_the_announcement_instead():
    room = _Channel(MUSIC_ROOM)
    started = _Channel(STARTED_IN)
    client = _Client(room, started)

    await _notify(client, routes_to=MUSIC_ROOM)("Now playing: x — u")

    assert room.sent == [("Now playing: x — u", True)]
    assert started.sent == []


async def test_the_route_is_resolved_per_post_not_captured_once():
    """The whole reason this is a send-time lookup: an operator who sets
    `music = true` mid-session gets it honoured on the NEXT announcement, not
    the next session. Capturing the channel when the closure was built is the
    behaviour this replaced."""
    room = _Channel(MUSIC_ROOM)
    started = _Channel(STARTED_IN)
    client = _Client(room, started)
    routed: list[str | None] = [None]

    notify = make_notify(
        client=lambda: client, guild_id=GUILD, fallback_channel_id=STARTED_IN,
        resolve_music_channel=lambda _g: routed[0],
    )

    await notify("before")
    routed[0] = MUSIC_ROOM
    await notify("after")

    assert started.sent == [("before", True)]
    assert room.sent == [("after", True)]


async def test_an_unroutable_music_room_falls_back_rather_than_dropping():
    """`policy.toml` accepted the room, but the channel is gone or the bot
    lost access. A misrouted announcement is recoverable; a session that goes
    silent is the failure the notify path exists to prevent."""
    started = _Channel(STARTED_IN)
    client = _Client(started)  # MUSIC_ROOM deliberately absent

    await _notify(client, routes_to=MUSIC_ROOM)("Music stopped — voice connection lost.")

    assert started.sent == [("Music stopped — voice connection lost.", True)]


async def test_nothing_resolvable_drops_the_announcement_without_raising():
    """A `Notify` that raises would propagate into `MusicPlayerManager`'s
    transition and could tear a live session down over a missing channel."""
    client = _Client()

    await _notify(client, routes_to=MUSIC_ROOM)("Now playing: x — u")

    assert client.lookups == [int(MUSIC_ROOM), int(STARTED_IN)]


async def test_no_live_client_drops_the_announcement_without_raising():
    """Mid-gateway-reconnect: `current_client()` is `None`. The closure is
    handed a GETTER for exactly this window, and must survive it."""
    notify = make_notify(
        client=lambda: None, guild_id=GUILD, fallback_channel_id=STARTED_IN,
    )

    await notify("Now playing: x — u")  # no exception is the assertion


async def test_a_non_messageable_channel_is_treated_as_absent():
    """A category (or any channel that takes no messages) resolving from the
    cache must read the same as not resolving at all — the caller has one
    fallback path, not two."""

    class _Category:  # not a `discord.abc.Messageable`
        id = int(MUSIC_ROOM)

    started = _Channel(STARTED_IN)

    class _MixedClient(_Client):
        def get_channel(self, channel_id: int):
            self.lookups.append(channel_id)
            return _Category() if channel_id == int(MUSIC_ROOM) else started

    await _notify(_MixedClient(), routes_to=MUSIC_ROOM)("Now playing: x — u")

    assert started.sent == [("Now playing: x — u", True)]


@pytest.mark.parametrize("bad", ["", "not-a-snowflake"])
async def test_an_unusable_channel_id_is_absent_not_a_crash(bad: str):
    """Both halves come from outside — a persisted row and a `policy.toml`
    line — so neither is guaranteed to be an int."""
    client = _Client()

    await _notify(client, routes_to=bad, fallback=bad)("Now playing: x — u")

    assert client.lookups == []


def test_no_music_room_answers_none_for_every_guild():
    """The unwired default has to be the pre-flag behaviour for ANY guild, not
    just the ones a test names."""
    assert no_music_room(GUILD) is None
    assert no_music_room("999999999999999999") is None
