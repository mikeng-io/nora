"""`music_notify.make_notify` — WHERE a music announcement lands.

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

from nora.platform.discord.music_notify import make_notify, no_music_channel

GUILD = "100000000000000203"
STARTED_IN = "100000000000000204"
MUSIC_CHANNEL = "1480387369830973999"


class _Channel(discord.abc.Messageable):
    """A real `Messageable` whose `send` records instead of posting.

    Carries a `guild`, because `_messageable` checks it — a channel object
    with no guild is a DM, and `discord.abc.Messageable` covers those too."""

    def __init__(self, channel_id: str, guild_id: str = GUILD, *, raises=None) -> None:
        self.id = int(channel_id)
        self.guild = _Guild(guild_id)
        self.sent: list[tuple[str, bool]] = []
        self._raises = raises

    async def _get_channel(self):  # the one abstract member
        return self

    async def send(self, content=None, **kwargs):  # type: ignore[override]
        if self._raises is not None:
            raise self._raises
        self.sent.append((content, bool(kwargs.get("silent"))))
        return None


class _Guild:
    def __init__(self, guild_id: str) -> None:
        self.id = int(guild_id)


class _DmChannel(discord.abc.Messageable):
    """A `Messageable` with NO `guild` — `ConnectionState.get_channel` checks
    private channels FIRST, so a DM channel id in `music_channel` would
    otherwise have posted a guild's announcements as a DM."""

    def __init__(self, channel_id: str) -> None:
        self.id = int(channel_id)
        self.sent: list[str] = []

    async def _get_channel(self):
        return self

    async def send(self, content=None, **kwargs):  # type: ignore[override]
        self.sent.append(content)
        return None


class _Client:
    """`get_channel` over a fixed id → channel map; anything else is absent,
    which is what a deleted channel or one the bot lost access to looks
    like. `closed` models a client `DiscordAdapter.listen` has shut down —
    discord.py keeps the channel cache on close, so a closed client still
    RESOLVES channels."""

    def __init__(self, *channels, closed: bool = False) -> None:
        self._by_id = {c.id: c for c in channels}
        self.lookups: list[int] = []
        self._closed = closed

    def is_closed(self) -> bool:
        return self._closed

    def get_channel(self, channel_id: int):
        self.lookups.append(channel_id)
        return self._by_id.get(channel_id)


def _notify(client, *, routes_to: str | None = None, fallback: str | None = STARTED_IN):
    return make_notify(
        client=lambda: client,
        guild_id=GUILD,
        fallback_channel_id=fallback,
        resolve_music_channel=(
            (lambda _g: routes_to) if routes_to is not None else no_music_channel
        ),
    )


async def test_with_no_music_channel_it_announces_where_the_session_started():
    """A deployment that never names a `music_channel` announces where the session
    started."""
    started = _Channel(STARTED_IN)
    client = _Client(started)

    await _notify(client)("Now playing: x — u")

    assert started.sent == [("Now playing: x — u", True)]


async def test_a_named_music_channel_takes_the_announcement_instead():
    room = _Channel(MUSIC_CHANNEL)
    started = _Channel(STARTED_IN)
    client = _Client(room, started)

    await _notify(client, routes_to=MUSIC_CHANNEL)("Now playing: x — u")

    assert room.sent == [("Now playing: x — u", True)]
    assert started.sent == []


async def test_the_route_is_resolved_per_post_not_captured_once():
    """The whole reason this is a send-time lookup: an operator who names a
    `music_channel` mid-session gets it honoured on the NEXT announcement, not
    the next session. Capturing the channel when the closure is built would miss it."""
    room = _Channel(MUSIC_CHANNEL)
    started = _Channel(STARTED_IN)
    client = _Client(room, started)
    routed: list[str | None] = [None]

    notify = make_notify(
        client=lambda: client, guild_id=GUILD, fallback_channel_id=STARTED_IN,
        resolve_music_channel=lambda _g: routed[0],
    )

    await notify("before")
    routed[0] = MUSIC_CHANNEL
    await notify("after")

    assert started.sent == [("before", True)]
    assert room.sent == [("after", True)]


async def test_an_unroutable_music_channel_falls_back_rather_than_dropping():
    """`policy.toml` accepted the id, but the channel is gone or the bot lost
    access. A misrouted announcement is recoverable; a session that goes
    silent is the failure the notify path exists to prevent."""
    started = _Channel(STARTED_IN)
    client = _Client(started)  # MUSIC_CHANNEL deliberately absent

    await _notify(client, routes_to=MUSIC_CHANNEL)("Music stopped — voice connection lost.")

    assert started.sent == [("Music stopped — voice connection lost.", True)]


async def test_nothing_resolvable_drops_the_announcement_without_raising():
    """A `Notify` that raises would propagate into `MusicPlayerManager`'s
    transition and could tear a live session down over a missing channel."""
    client = _Client()

    await _notify(client, routes_to=MUSIC_CHANNEL)("Now playing: x — u")

    assert client.lookups == [int(MUSIC_CHANNEL), int(STARTED_IN)]


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
        id = int(MUSIC_CHANNEL)

    started = _Channel(STARTED_IN)

    class _MixedClient(_Client):
        def get_channel(self, channel_id: int):
            self.lookups.append(channel_id)
            return _Category() if channel_id == int(MUSIC_CHANNEL) else started

    await _notify(_MixedClient(), routes_to=MUSIC_CHANNEL)("Now playing: x — u")

    assert started.sent == [("Now playing: x — u", True)]


@pytest.mark.parametrize("bad", ["", "not-a-snowflake"])
async def test_an_unusable_channel_id_is_absent_not_a_crash(bad: str):
    """Both halves come from outside — a persisted row and a `policy.toml`
    line — so neither is guaranteed to be an int."""
    client = _Client()

    await _notify(client, routes_to=bad, fallback=bad)("Now playing: x — u")

    assert client.lookups == []


def test_no_music_channel_answers_none_for_every_guild():
    """The unwired default has to be the pre-field behaviour for ANY guild, not
    just the ones a test names."""
    assert no_music_channel(GUILD) is None
    assert no_music_channel("999999999999999999") is None


# --- the send half: nothing here may raise into a manager transition ---------
# `_notify_now_playing` awaits this closure at the end of `skip()` and
# `on_track_finished()` and guards neither. An escape would leave `/music skip`'s
# deferred interaction unresolved while the skip had happened, and make every
# natural track-end return HTTP 500 to the audio-controller with the event
# unconsumed.


async def test_a_forbidden_send_falls_back_instead_of_raising():
    """The realistic permission mistake: View Channel granted on the dedicated
    announcements channel, Send Messages forgotten. That is NOT a resolution
    failure — the channel resolves fine — so the resolution-time fallback
    never covered it."""
    forbidden = _Channel(
        MUSIC_CHANNEL,
        raises=discord.Forbidden(_Response(403), "Missing Permissions"),
    )
    started = _Channel(STARTED_IN)
    client = _Client(forbidden, started)

    await _notify(client, routes_to=MUSIC_CHANNEL)("Now playing: x — u")

    assert started.sent == [("Now playing: x — u", True)]


async def test_a_send_with_an_unknown_outcome_is_not_repeated_in_the_fallback_channel():
    """A 5xx or a dropped connection can follow a message that DID post.
    Retrying it in the session's own channel would show it twice."""
    flaky = _Channel(MUSIC_CHANNEL, raises=discord.HTTPException(_Response(502), "bad gateway"))
    started = _Channel(STARTED_IN)
    client = _Client(flaky, started)

    await _notify(client, routes_to=MUSIC_CHANNEL)("Now playing: x — u")

    assert started.sent == []


async def test_both_channels_refusing_the_send_still_does_not_raise():
    both_forbidden = _Client(
        _Channel(MUSIC_CHANNEL, raises=discord.Forbidden(_Response(403), "nope")),
        _Channel(STARTED_IN, raises=discord.Forbidden(_Response(403), "nope")),
    )

    await _notify(both_forbidden, routes_to=MUSIC_CHANNEL)("Now playing: x — u")


async def test_a_resolver_that_raises_does_not_escape():
    """`resolve_music_channel` is caller-supplied, so it is in the blast radius
    too — an escape from it lands in the same unguarded transition."""
    notify = make_notify(
        client=lambda: _Client(_Channel(STARTED_IN)),
        guild_id=GUILD,
        fallback_channel_id=STARTED_IN,
        resolve_music_channel=lambda _g: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    await notify("Now playing: x — u")


async def test_a_client_getter_that_raises_does_not_escape():
    def _boom():
        raise RuntimeError("no client")

    await make_notify(
        client=_boom, guild_id=GUILD, fallback_channel_id=STARTED_IN,
    )("Now playing: x — u")


# --- the guild check: the one thing only send time can decide ----------------


async def test_a_channel_in_another_guild_is_refused_and_falls_back():
    """`policy.toml` refuses a `<guild>/<channel>` pair, but a BARE snowflake
    from another guild has the identical shape and cannot be told apart there.
    `Client.get_channel` searches every guild the bot is in, so without this
    check one server's listening activity announces in another's channel —
    permanently, with the `falling back to` warning never firing because
    resolution SUCCEEDED."""
    other_guild = _Channel(MUSIC_CHANNEL, guild_id="999999999999999999")
    started = _Channel(STARTED_IN)
    client = _Client(other_guild, started)

    await _notify(client, routes_to=MUSIC_CHANNEL)("Now playing: x — u")

    assert other_guild.sent == []
    assert started.sent == [("Now playing: x — u", True)]


async def test_a_dm_channel_is_refused():
    """`ConnectionState.get_channel` checks private channels FIRST, and
    `DMChannel` is `Messageable` — so a DM id would have posted the guild's
    announcements as a direct message."""
    dm = _DmChannel(MUSIC_CHANNEL)
    started = _Channel(STARTED_IN)
    client = _Client(dm, started)

    await _notify(client, routes_to=MUSIC_CHANNEL)("Now playing: x — u")

    assert dm.sent == []
    assert started.sent == [("Now playing: x — u", True)]


async def test_a_fallback_channel_in_another_guild_is_refused_too():
    """The fallback comes from a persisted row that outlived whatever wrote
    it, so it gets the same check as the routed channel rather than being
    trusted for being older."""
    started = _Channel(STARTED_IN, guild_id="999999999999999999")
    client = _Client(started)

    await _notify(client)("Now playing: x — u")  # nothing posted, nothing raised
    assert started.sent == []


# --- the closed-client check -------------------------------------------------


async def test_a_closed_client_is_treated_as_no_client():
    """discord.py deliberately does NOT clear its channel cache on close
    ("users rely on cache being available post-close"), so a closed client
    passes an `is None` guard, resolves a real channel, and fails at `send`.
    `DiscordAdapter.listen` builds a fresh client per attempt and closes the
    old one while this closure lives on in `MusicPlayerManager._notify`, and
    nothing re-registers it — so without this the failure is every
    announcement for the rest of the session."""
    started = _Channel(STARTED_IN)
    client = _Client(started, closed=True)

    await _notify(client)("Now playing: x — u")

    assert started.sent == []
    assert client.lookups == []


class _Response:
    """The minimum `discord.HTTPException` reads off an aiohttp response."""

    def __init__(self, status: int) -> None:
        self.status = status
        self.reason = "Forbidden"
