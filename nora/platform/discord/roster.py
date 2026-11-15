"""`DiscordRoster` — who is in one Discord room, over the REST members list.

`nora.domain.ports.RosterSource`'s Discord implementation, mirroring
`nora.platform.signal.roster.SignalRoster`'s contract exactly: it selects ONE
conversation by id and it NEVER RAISES. The caller is a room-scoped tool
running inside a live turn, so an unreachable API has to become
`Roster(available=False)` — "I could not look" — and never an empty room,
which would read as "there is nobody here" (R-5, and `Roster`'s own docstring).

WHY THE GUILD'S MEMBERS, NOT THE CHANNEL'S. Discord has no "who is in this
channel" endpoint; channel access is computed from role and per-channel
permission overwrites, which would mean reading every role and every overwrite
and re-implementing Discord's permission resolution to get an answer it does
not itself expose. `GET /guilds/{id}/members` is the real, readable set.

That over-reports for a restricted channel — the roster may name people who
cannot actually see this room. For the disclosure invariant that is the SAFE
direction and not merely an acceptable one: R-21/R-31 ask whether the room's
people are a SUBSET of a Life360 circle, so a LARGER reported room can only
ever make `where_is` refuse where it might otherwise have spoken. It can never
cause a disclosure that a true channel roster would have blocked. The reverse
error — under-reporting, then disclosing to somebody the roster forgot — is the
one that would be unsafe, and this cannot make it.

REQUIRES THE GUILD_MEMBERS PRIVILEGED INTENT, toggled on for the application in
the Discord Developer Portal. Without it this endpoint answers 403 and every
room-scoped tool degrades to `unavailable` rather than guessing — the honest
failure, and one an operator can act on from the log line.
"""

from __future__ import annotations

import asyncio
import logging

import aiohttp

from ...domain.ports import Roster

log = logging.getLogger("nora.discord.roster")

DEFAULT_API_BASE = "https://discord.com/api/v10"

# Matches `SignalRoster`'s budget and for its reason: two failed reads still fit
# inside a single model round trip, which is the unit a wedged dependency has to
# stay cheap relative to.
DEFAULT_TIMEOUT_SECONDS = 4.0

# The provider's maximum page size; a full page requires another read.
MAX_MEMBERS_PER_PAGE = 1000


def _member_ids(body: object) -> list[str] | None:
    if not isinstance(body, list) or len(body) > MAX_MEMBERS_PER_PAGE:
        return None
    ids = []
    for entry in body:
        user = entry.get("user") if isinstance(entry, dict) else None
        value = user.get("id") if isinstance(user, dict) else None
        if not isinstance(value, str) or not value.isascii() or not value.isdigit():
            return None
        ids.append(value)
    return ids


class DiscordRoster:
    """Satisfies `nora.domain.ports.RosterSource` for Discord."""

    platform = "discord"

    def __init__(
        self,
        token: str,
        *,
        base_url: str = DEFAULT_API_BASE,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async def roster(self, *, conversation_id: str) -> Roster:
        """The members of the guild `conversation_id` belongs to.

        `conversation_id` is `"<guild_id>/<channel_id>"` (policy.example.toml).
        The guild half is what identifies the member list; the channel half is
        not addressable here — see the module docstring.
        """
        guild_id, _, _channel_id = conversation_id.partition("/")
        if not (guild_id.isascii() and guild_id.isdigit()) or not _channel_id:
            # Not the documented shape. Unavailable, not empty: a malformed id
            # is a wiring fault, and answering "nobody is here" would hide it.
            # Only a snowflake reaches the request path.
            log.warning(
                "conversation_id %r is not '<guild_id>/<channel_id>'; roster unavailable",
                conversation_id,
            )
            return Roster(available=False)

        headers = {"Authorization": f"Bot {self._token}"}
        members: list[frozenset[str]] = []
        after = 0
        try:
            async with (
                asyncio.timeout(self._timeout.total),
                aiohttp.ClientSession(timeout=self._timeout) as session,
            ):
                while True:
                    params = {"limit": str(MAX_MEMBERS_PER_PAGE)}
                    if after:
                        params["after"] = str(after)
                    async with session.get(
                        f"{self._base_url}/guilds/{int(guild_id)}/members",
                        headers=headers, params=params, allow_redirects=False,
                    ) as resp:
                        if resp.status == 403:
                            log.error(
                                "roster read forbidden for guild %s — the GUILD_MEMBERS "
                                "privileged intent is probably off for this application; "
                                "room-scoped tools stay unavailable until it is enabled",
                                guild_id,
                            )
                            return Roster(available=False)
                        if resp.status != 200:
                            log.warning("roster read returned HTTP %s", resp.status)
                            return Roster(available=False)
                        ids = _member_ids(await resp.json())
                    if ids is None:
                        log.error("roster read has malformed member rows; refusing partial roster")
                        return Roster(available=False)
                    numeric = [int(value) for value in ids]
                    if len(set(numeric)) != len(numeric) or any(i <= after for i in numeric):
                        log.error(
                            "roster pagination repeated or regressed; refusing partial roster"
                        )
                        return Roster(available=False)
                    members.extend(frozenset({value}) for value in ids)
                    if len(ids) < MAX_MEMBERS_PER_PAGE:
                        return Roster(available=True, members=tuple(members))
                    after = max(numeric)
        except (TimeoutError, aiohttp.ClientError, ValueError, TypeError):
            # The total timeout covers ALL pages. A failed later page cannot
            # turn the already read prefix into a complete disclosure roster.
            log.warning("roster read failed; refusing partial roster")
            return Roster(available=False)
