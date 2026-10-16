"""`DiscordPresence` — the `DiscordPresenceSource` port's real
implementation.
Answers "what is this person's Discord presence right now" — per-person
status (`online` / `idle` / `dnd` / `offline`) plus rich presence (game
title, listening activity) — from the LIVE client's guild cache, pointed
at every guild the client can see because the `online_now` question is
room-scoped, not channel-scoped.

"Discord online" is `member.status` and
`member.activities`, NOT voice-channel membership — a member typing in a
forum thread is online on Discord and in no voice channel. Voice
membership stays with `DiscordVoicePresence`, the narrow `voice` sub-arm.

Needs the `presences` privileged intent, which `adapter.py` enables —
without it `member.status` reads `offline` for everyone and the rich
fields never populate.

NEVER raises — matches `DiscordVoicePresence`'s discipline: no live
client, a guild-cache miss, or anything discord.py throws mid-scan is
`None` — the WHOLE READ unavailable (R-5) — and a member the client
cannot see is an ABSENT KEY, their `nothing_found`. The two never share
a shape.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

import discord

from nora.domain.ports import DiscordPresenceState
from nora.platform.discord.adapter import DiscordAdapter

log = logging.getLogger("nora.presence")


class DiscordPresence:
    """Satisfies `nora.domain.ports.DiscordPresenceSource`. NEVER raises."""

    def __init__(self, *, adapter: DiscordAdapter) -> None:
        self._adapter = adapter

    async def presence(
        self, *, member_ids: frozenset[str],
    ) -> dict[str, DiscordPresenceState] | None:
        """Live per-member presence for `member_ids`, keyed by Discord user
        id. None when there is no live client or the scan failed; a member
        the client cannot see is simply absent."""
        if not member_ids:
            return {}
        try:
            return self._scan(member_ids=member_ids)
        except Exception:  # the never-raises backstop (R-5)
            log.exception("discord presence scan failed")
            return None

    def _scan(
        self, *, member_ids: frozenset[str],
    ) -> dict[str, DiscordPresenceState] | None:
        client = self._adapter.current_client()
        if client is None:
            return None
        wanted = set(member_ids)
        out: dict[str, DiscordPresenceState] = {}
        for guild in client.guilds:
            for member_id in sorted(wanted):
                try:
                    member = guild.get_member(int(member_id))
                except ValueError:
                    continue  # not a Discord snowflake at all
                if member is None:
                    continue
                out[member_id] = DiscordPresenceState(
                    status=str(member.status),
                    **_rich_presence(member.activities),
                )
                wanted.discard(member_id)
        return out


def _rich_presence(
    activities: Iterable[
        discord.Activity | discord.Game | discord.CustomActivity
        | discord.Streaming | discord.Spotify
    ] | None,
) -> dict[str, str | None]:
    """The rich-presence half of one member's activities: a `Game` title,
    a listening activity (Spotify's own, or any `listening`-typed one),
    and the custom status text. Only fields the activity actually names
    are taken — present-optional, never load-bearing."""
    game: str | None = None
    listening: str | None = None
    custom: str | None = None
    for act in activities or ():
        if isinstance(act, discord.Game):
            if game is None:
                game = act.name
        elif isinstance(act, discord.Spotify):
            if listening is None:
                listening = act.title
        elif isinstance(act, discord.CustomActivity):
            if custom is None:
                custom = act.name
        elif (listening is None
                and getattr(act, "type", None) == discord.ActivityType.listening):
            listening = act.name
    return {"game": game, "listening": listening, "custom_status": custom}
