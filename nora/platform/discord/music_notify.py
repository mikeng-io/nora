"""The ONE place a music announcement's destination is decided.

Three surfaces announce music: `/music play`
(`music_commands._notify_factory`, off `interaction.channel`), the agent tool
bridge (`DiscordMusicToolBridge._notify`, off the `conversation_id`'s channel
half) and boot resume (`music_commands.resume_persisted_sessions`, off the
persisted row). Left to themselves they would each answer "which channel" a
different way and agree only by accident. `music_messages.py` does this for
the WORDING of a music message; this module does it for the DESTINATION, and
for the same reason: a surface can only change where an announcement lands
by changing THIS file.

**Resolved at send time, not at capture time.** A closure that captured a
channel when the session started would be stuck with it for the session's
whole life. Resolving per-post means a boot resume honours a `music_channel`
set while Nora was down (fresh policy, old session), and there is one code
path for all three surfaces. It does NOT
mean an operator edit lands mid-session — `load_policy` runs once per process
and `Policy` is frozen, so within a process the resolver is a constant
function. An edit needs a restart, exactly as `voice = true` does.

**Announcements only** (`MusicPlayerManager._notify_now_playing` and
`_announce_teardown`). A slash command still replies where it was invoked —
the person who typed it is looking there — and Nora's conversational reply on
the tag path still lands in the room she was tagged in.

**Nothing here may raise.** `Notify` is awaited inside
`MusicPlayerManager`'s transitions — `_notify_now_playing` at the end of
`skip()` and `on_track_finished()` — and neither guards it. An escape would
propagate all the way out: a `Forbidden` from `send` would leave
`/music skip`'s deferred interaction unresolved ("The application did not
respond") while the skip had actually happened, and turn every natural
track-end into an HTTP 500 back to the audio-controller with the event
unconsumed, freezing the `last_event_seq` baseline. The manager already
treats a `Notify` as non-raising in `_announce_teardown` (which wraps it);
this module is where that becomes true rather than assumed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import discord

log = logging.getLogger("nora.music")

#: A Discord snowflake is a 64-bit id, so 17-20 decimal digits in practice.
#: The bound is a sanity check on a hand-copied value, not a parser.
_SNOWFLAKE_MIN_DIGITS = 17
_SNOWFLAKE_MAX_DIGITS = 20

#: Maps a Discord guild id to the CHANNEL id its music announcements belong
#: in, or `None` for "no room named one" — `nora.policy.Policy.
#: music_channel_for`, threaded as a callable rather than as the `Policy`
#: itself so the platform layer keeps knowing nothing about `nora.policy`
#: (`_voice_room_of`/`resolve_voice_room` in `nora/web/voice.py` set this
#: precedent; `pyproject.toml`'s import-linter contract is what makes the
#: claim a build failure rather than a docstring).
ResolveMusicChannel = Callable[[str], str | None]

#: Answers "the live Discord client, right now" — `DiscordAdapter.
#: current_client`. A GETTER and never a captured client: see `make_notify`.
ClientGetter = Callable[[], "discord.Client | None"]


def no_music_channel(_guild_id: str) -> None:
    """The unwired default: no guild names a channel, so every announcement
    falls back to where its session started — the behaviour without a
    `music_channel`. Named rather than inlined as `lambda _: None` so a
    caller that has no policy to thread says so."""
    return None


def is_snowflake(value: str) -> bool:
    """Whether `value` is usable as a Discord channel id.

    ASCII decimal digits with a plausible snowflake length — NOT
    `str.isdigit()`, which is fail-open in both directions: it accepts
    superscripts (`"²"`) and other non-decimal digit characters that `int()`
    then refuses, so a line that can NEVER route would pass startup and fail at
    send time indistinguishably from a deleted channel — PASS standing in for
    ERROR. It also accepts Arabic-Indic digits, which `int()` DOES parse, so
    such an id would silently route while reading as something else entirely.

    The length bound additionally rejects `"0"` and a 30-digit number, both
    of which parse and neither of which can name a channel."""
    return (
        value.isascii()
        and value.isdecimal()
        and _SNOWFLAKE_MIN_DIGITS <= len(value) <= _SNOWFLAKE_MAX_DIGITS
    )


def make_notify(
    *,
    client: Callable[[], discord.Client | None],
    guild_id: str,
    fallback_channel_id: str | None,
    resolve_music_channel: ResolveMusicChannel = no_music_channel,
):
    """The `MusicPlayerManager.Notify` closure for one guild's session.

    `client` is a getter, not a client, and callers must pass a REAL one
    (`DiscordAdapter.current_client`) rather than a lambda over a captured
    one. `DiscordAdapter.listen` builds a fresh `discord.Client` per
    connection attempt and closes the old one, while this closure lives in
    `MusicPlayerManager._notify` and outlives the attempt — and a closed
    client is not `None`: discord.py deliberately does not clear its cache on
    close ("users rely on cache being available post-close"), so
    `get_channel` still answers with a real channel object whose `send` then
    fails. That is why `_live_client` below tests `is_closed()` too.

    `fallback_channel_id` is "the channel where it started" — the persisted
    `MusicSession.notify_channel_id`. It stays the fallback rather than
    becoming dead weight: a guild naming no `music_channel` announces there,
    and a guild WITH one still needs somewhere to go if that
    channel stops resolving.
    """

    async def notify(text: str) -> None:
        try:
            await _post(text)
        except Exception:
            # The whole body, not just the send: `resolve_music_channel` and
            # the `client` getter are caller-supplied too, and an escape from
            # any of them lands in the same unguarded transition. See the
            # module docstring for what one actually cost.
            log.exception(
                "music notify: announcement for guild %s failed and was dropped "
                "(text: %r)", guild_id, text,
            )

    async def _post(text: str) -> None:
        live = _live_client(client)
        if live is None:
            log.warning(
                "music notify: no usable Discord client for guild %s — "
                "announcement dropped (text: %r)", guild_id, text,
            )
            return
        routed = resolve_music_channel(guild_id)
        channel = _messageable(live, routed, guild_id) if routed is not None else None
        if routed is not None and channel is None:
            # The operator named a channel `policy.toml` accepted, but it is
            # not usable from here — deleted, access lost, or in another
            # guild. Fall back rather than drop: a misrouted announcement is
            # recoverable, a silent session is the failure this whole path
            # exists to prevent.
            log.warning(
                "music notify: guild %s routes to channel %s (policy.toml "
                "music_channel) but it is not usable — falling back to %s",
                guild_id, routed, fallback_channel_id,
            )
        fallback = _messageable(live, fallback_channel_id, guild_id)
        if channel is None:
            channel = fallback
        if channel is None:
            log.warning(
                "music notify: guild %s has no usable channel "
                "(routed=%s, fallback=%s) — announcement dropped (text: %r)",
                guild_id, routed, fallback_channel_id, text,
            )
            return
        if await _send(channel, text, guild_id) != _REFUSED:
            # Sent, or UNKNOWN (a 5xx / dropped connection): the message may
            # already be in the channel, and posting it to a second channel
            # would duplicate it. Only a definite refusal falls back.
            return
        # The send itself failed — the routed channel resolves but refuses
        # writes, which is exactly the shape a DEDICATED announcements
        # channel has when the operator granted View and forgot Send. This is
        # NOT a resolution failure, so the fallback above never ran for it;
        # try it here before giving up.
        if fallback is not None and fallback is not channel:
            log.warning(
                "music notify: guild %s could not post to channel %s — "
                "retrying the session's own channel %s",
                guild_id, routed, fallback_channel_id,
            )
            await _send(fallback, text, guild_id)

    return notify


_SENT = "sent"
#: Discord definitely did not post it (403 missing permission, 404 channel gone).
_REFUSED = "refused"
#: The send raised but the message may have landed (5xx, dropped connection).
_UNKNOWN = "unknown"


async def _send(channel: discord.abc.Messageable, text: str, guild_id: str) -> str:
    """`_SENT`, `_REFUSED` or `_UNKNOWN`, never an exception. `silent` keeps
    a "Now playing" line every few minutes out of everyone's push
    notifications. Only `_REFUSED` is safe to retry elsewhere."""
    try:
        await channel.send(text, silent=True)
    except (discord.Forbidden, discord.NotFound) as exc:
        log.warning(
            "music notify: send to channel %s refused for guild %s: %s",
            getattr(channel, "id", "?"), guild_id, exc,
        )
        return _REFUSED
    except discord.HTTPException as exc:
        log.warning(
            "music notify: send to channel %s failed for guild %s (outcome unknown, "
            "not retrying elsewhere): %s",
            getattr(channel, "id", "?"), guild_id, exc,
        )
        return _UNKNOWN
    log.info(
        "music notify: sent %r to channel %s (guild %s)",
        text, getattr(channel, "id", "?"), guild_id,
    )
    return _SENT


def _live_client(get: Callable[[], discord.Client | None]) -> discord.Client | None:
    """`None` for absent OR closed. A closed client is the dangerous one: it
    keeps its channel cache by design, so without this check it passes the
    `is None` guard, resolves a channel, and fails at `send` — on every
    announcement for the rest of the session, since `MusicPlayerManager`
    re-registers a notify closure only when a session is CREATED."""
    live = get()
    if live is None or live.is_closed():
        return None
    return live


def _messageable(
    client: discord.Client, channel_id: str | None, guild_id: str,
) -> discord.abc.Messageable | None:
    """`None` for every reason a channel id cannot carry this guild's
    announcements — unset, not a snowflake, not in the cache, not messageable,
    or **not in `guild_id`**. The caller treats all of them the same way, so
    they answer the same thing (`_member_voice_channel`'s discipline in
    `music_tool_bridge.py`).

    The guild check is the one that has to be here rather than in
    `load_policy`: a channel's guild is not knowable before a Discord client
    exists. `policy.toml` refuses a `<guild>/<channel>` pair, but a BARE
    snowflake from another guild has the identical shape — and
    `ConnectionState.get_channel` searches private channels first and then
    every guild the bot is in, so without this it resolves happily. A DM
    channel is `Messageable` too, so one guild's `Now playing:` lines could
    have been posted into another guild, or as a DM.
    Digits-only blocked the SYNTAX that expresses cross-guild routing, never
    the capability."""
    if channel_id is None or not is_snowflake(channel_id):
        return None
    channel = client.get_channel(int(channel_id))
    if not isinstance(channel, discord.abc.Messageable):
        return None
    guild = getattr(channel, "guild", None)
    if guild is None or str(guild.id) != guild_id:
        log.warning(
            "music notify: channel %s is not in guild %s (guild=%s) — refusing "
            "to post one guild's announcements outside it",
            channel_id, guild_id, getattr(guild, "id", None),
        )
        return None
    return channel
