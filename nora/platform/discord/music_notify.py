"""The ONE place a music announcement's destination is decided
(design/discord-music/10-notification-routing.md).

Three surfaces used to build their own notify closure — `/music play`
(`music_commands._notify_factory`, off `interaction.channel`), the agent tool
bridge (`DiscordMusicToolBridge._notify`, off the `conversation_id`'s channel
half) and boot resume (`music_commands.resume_persisted_sessions`, off the
persisted row) — three closures that each answered "which channel" a
different way and only agreed by accident. `music_messages.py` did this for
the WORDING of a music message; this module does it for the DESTINATION, and
for the same reason: a surface can only change where an announcement lands
by changing THIS file.

**Resolved at send time, not at capture time.** The old closures captured a
channel when the session started and were then stuck with it for the
session's whole life — which is why the routing was invisible and had no way
to be corrected short of ending playback. `policy.toml`'s `music = true` is
a configuration fact, not a session fact, so it is read on every post: an
operator edit plus a reload takes effect on the next announcement instead of
the next session.

Announcements only (`MusicPlayerManager._notify_now_playing` and
`_announce_teardown`). A slash command still replies where it was invoked —
the person who typed it is looking there — and Nora's conversational reply on
the tag path still lands in the room she was tagged in
(design/discord-music/09-message-conventions.md's announcement contract is
unchanged; only the notify path's channel moves).
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import discord

log = logging.getLogger("nora.music")

#: Maps a Discord guild id to the CHANNEL id its music announcements belong
#: in, or `None` for "no room opted in" — `nora.policy.Policy.music_room`'s
#: channel half, threaded as a callable rather than as the `Policy` itself so
#: the platform layer keeps knowing nothing about `nora.policy`
#: (`_voice_room_of`/`resolve_voice_room` in `nora/web/voice.py` set this
#: precedent).
ResolveMusicChannel = Callable[[str], str | None]


def no_music_room(_guild_id: str) -> None:
    """The unwired default: no guild has a music room, so every announcement
    falls back to where its session started — the pre-`music = true`
    behaviour, exactly. Named rather than inlined as `lambda _: None` so a
    caller that has no policy to thread says so."""
    return None


def make_notify(
    *,
    client: Callable[[], discord.Client | None],
    guild_id: str,
    fallback_channel_id: str | None,
    resolve_music_channel: ResolveMusicChannel = no_music_room,
):
    """The `MusicPlayerManager.Notify` closure for one guild's session.

    `client` is a getter, not a client: the bridge's live client changes
    across a gateway reconnect (`DiscordAdapter.current_client()` can even be
    `None` mid-reconnect), and a captured one would go stale exactly when a
    long session needs it most.

    `fallback_channel_id` is "the channel where it started" — the persisted
    `MusicSession.notify_channel_id`. It stays the fallback rather than
    becoming dead weight: a guild with no `music = true` room keeps the old
    behaviour, and a guild WITH one still needs somewhere to go if that room
    stops resolving.
    """

    async def notify(text: str) -> None:
        live = client()
        if live is None:
            log.warning(
                "music notify: no live Discord client for guild %s — "
                "announcement dropped (text: %r)", guild_id, text,
            )
            return
        routed = resolve_music_channel(guild_id)
        channel = _messageable(live, routed) if routed is not None else None
        if routed is not None and channel is None:
            # The operator named a room `policy.toml` accepted, but the
            # client cannot post to it right now — the channel was deleted,
            # or the bot lost access to it. Fall back rather than drop: a
            # misrouted announcement is recoverable, a silent session is the
            # failure this whole path exists to prevent (found live
            # 2026-09-15, `connection_lost_line`'s own story).
            log.warning(
                "music notify: guild %s routes to channel %s (policy.toml "
                "music = true) but it is not messageable — falling back to %s",
                guild_id, routed, fallback_channel_id,
            )
        if channel is None:
            channel = _messageable(live, fallback_channel_id)
        if channel is None:
            log.warning(
                "music notify: guild %s has no messageable channel "
                "(routed=%s, fallback=%s) — announcement dropped (text: %r)",
                guild_id, routed, fallback_channel_id, text,
            )
            return
        log.info(
            "music notify: sending %r to channel %s (guild %s, routed=%s)",
            text, getattr(channel, "id", "?"), guild_id, routed is not None,
        )
        # Silent: a "Now playing" line every few minutes is useful to have in
        # the channel, not worth a push notification per track.
        await channel.send(text, silent=True)

    return notify


def _messageable(
    client: discord.Client, channel_id: str | None,
) -> discord.abc.Messageable | None:
    """`None` for every reason a channel id cannot be posted to — unset, not
    a number, not in the cache, or a category channel that takes no messages.
    The caller treats all of them the same way, so they answer the same thing
    (`_member_voice_channel`'s discipline in `music_tool_bridge.py`)."""
    if not channel_id:
        return None
    try:
        channel = client.get_channel(int(channel_id))
    except ValueError:
        return None
    return channel if isinstance(channel, discord.abc.Messageable) else None
