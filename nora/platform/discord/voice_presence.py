"""`DiscordVoicePresence` — the `VoicePresenceSource` port's real
implementation. Answers "who is in a voice channel
right now" from the LIVE client's guild cache — the same `member.voice`
read `DiscordMusicToolBridge` makes before joining, pointed at every guild
the client can see instead of one conversation's guild, because the
`online_now` question is room-scoped, not channel-scoped.

Zero new intents: `voice_states` is already in `Intents.default()`
(`adapter.py`), which is what keeps `member.voice` populated. Voice
membership answers ONLY the narrow "who is in which voice channel"
question — it does not stand in for "Discord online"; per-person status +
rich presence is `discord_presence.py`'s read, behind the `presences` intent.

NEVER raises — matches `TrackSource`/`WeatherProvider`'s discipline
(`nora/domain/ports.py`): no live client, a guild-cache miss, or anything
discord.py throws mid-scan is `None` — the WHOLE READ unavailable (R-5) —
and a member simply not in a voice channel is an ABSENT KEY, their
`nothing_found`. The two never share a shape.
"""

from __future__ import annotations

import logging

import discord

from nora.domain.ports import VoiceChannelRef
from nora.platform.discord.adapter import DiscordAdapter

log = logging.getLogger("nora.voice")


class DiscordVoicePresence:
    """Satisfies `nora.domain.ports.VoicePresenceSource`. NEVER raises."""

    def __init__(self, *, adapter: DiscordAdapter) -> None:
        self._adapter = adapter

    async def in_voice(
        self, *, member_ids: frozenset[str],
    ) -> dict[str, VoiceChannelRef] | None:
        """Live voice memberships for `member_ids`, keyed by Discord user
        id. None when there is no live client or the scan failed; a member
        not in any voice channel is simply absent."""
        if not member_ids:
            return {}
        try:
            return self._scan(member_ids=member_ids)
        except Exception:  # the never-raises backstop (R-5)
            log.exception("voice presence scan failed")
            return None

    def _scan(self, *, member_ids: frozenset[str]) -> dict[str, VoiceChannelRef] | None:
        client = self._adapter.current_client()
        if client is None:
            return None
        wanted = set(member_ids)
        out: dict[str, VoiceChannelRef] = {}
        for guild in client.guilds:
            for member_id in sorted(wanted):
                try:
                    member = guild.get_member(int(member_id))
                except ValueError:
                    continue  # not a Discord snowflake at all
                if member is None or member.voice is None:
                    continue
                channel = member.voice.channel
                # Voice channels only — a stage channel is not "in voice"
                # for this tool's purposes.
                if isinstance(channel, discord.VoiceChannel):
                    out[member_id] = VoiceChannelRef(
                        channel_id=str(channel.id),
                        channel_name=channel.name,
                    )
                    wanted.discard(member_id)
        return out
