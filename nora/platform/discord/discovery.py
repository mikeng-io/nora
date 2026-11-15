"""`DiscordDiscovery` — Discord's collection-roster capability for managed
onboarding (`identity.md` «Platform discovery and membership»).

WHAT DISCORD CAN PROVE. `GET /guilds/{id}/members` enumerates a GUILD's
member set — the roster is the guild's, and a guild is never a channel's
audience (`room_audience=False` — see `roster.py`'s module docstring for
why Discord has no "who is in this channel" read). Member add/remove/
update gateway events already stream as membership observations, and a
sender's snowflake types to `discord:user_id` without another read.

The `fetch` seam takes the row list so direct mode (`DiscordRoster.
guild_members`, the real paginated REST read) and bridge mode (the
bridge's own `members` read over `/v1/reads`) share this one
classification path — the bridge runs the same `DiscordRoster` upstream,
so either way the rows carry the provider's own `bot`/`system` flags
rather than a shape we guessed.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from nora.domain.identity import Identifier
from nora.domain.managed import (
    CollectionSnapshot,
    DiscoveryCapabilities,
    ObservedMember,
)

log = logging.getLogger("nora.discord.discovery")


def _snowflake(value: str) -> bool:
    return value.isascii() and value.isdigit()


def _observe(row: Any) -> ObservedMember:
    """Classify one guild member row by the provider's own `bot`/`system` flags."""
    user = row.get("user") if isinstance(row, dict) else None
    value = user.get("id") if isinstance(user, dict) else None
    if not isinstance(value, str) or not _snowflake(value) or user is None:
        return ObservedMember(frozenset(), "unknown")
    if user.get("bot") or user.get("system"):
        return ObservedMember(
            frozenset({Identifier("discord", "user_id", value)}), "bot")
    return ObservedMember(
        frozenset({Identifier("discord", "user_id", value)}))


class DiscordDiscovery:
    """`CollectionDiscovery` for Discord guilds.

    `fetch` is `async guild_id -> list[dict] | None`: the guild's raw
    member rows, or `None` when the read cannot complete (the same
    contract `DiscordRoster.guild_members` keeps). Injecting it keeps the
    transport choice — direct REST or the bridge's `/v1/reads` mirror —
    out of the normalization.
    """

    platform = "discord"

    capabilities = DiscoveryCapabilities(
        roster=True,
        member_events=True,
        sender_typing=True,
        # The only member set is the guild's — a superset that is never a
        # channel's audience, so `audience="room"` refuses to build rather
        # than grant a guild to a room.
        room_audience=False,
    )

    def __init__(
        self, *,
        fetch: Callable[[str], Awaitable[Sequence[dict[str, Any]] | None]],
    ) -> None:
        self._fetch = fetch

    def collection_of(self, *, conversation_id: str) -> str | None:
        guild_id = conversation_id.partition("/")[0]
        return guild_id if _snowflake(guild_id) else None

    def collection_ref(self, *, object_ref: str) -> str | None:
        kind, _, ref = object_ref.partition(":")
        return ref if kind == "guild" and _snowflake(ref) else None

    async def members(self, *, collection: str) -> CollectionSnapshot:
        rows = await self._fetch(collection)
        if rows is None:
            return CollectionSnapshot(
                available=False, detail="guild-members-unavailable")
        if not rows:
            # A guild can never enumerate to zero members — the application
            # itself is one. An empty answer is a broken read, not proof
            # everyone left, so it cannot stand in as a complete roster.
            return CollectionSnapshot(
                available=False, detail="empty-roster")
        return CollectionSnapshot(
            available=True,
            members=tuple(_observe(row) for row in rows),
            complete=True,
            observed_ms=int(time.time() * 1000),
        )

    async def sender_identifiers(
        self, *, collection: str, value: str
    ) -> frozenset[Identifier] | None:
        if not _snowflake(value):
            return None
        return frozenset({Identifier("discord", "user_id", value)})
