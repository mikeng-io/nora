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

import logging

import aiohttp

from ...domain.ports import Roster

log = logging.getLogger("nora.discord.roster")

DEFAULT_API_BASE = "https://discord.com/api/v10"

# Matches `SignalRoster`'s budget and for its reason: two failed reads still fit
# inside a single model round trip, which is the unit a wedged dependency has to
# stay cheap relative to.
DEFAULT_TIMEOUT_SECONDS = 4.0

# Discord's own maximum for this endpoint. A guild larger than this would need
# `after=` pagination; `_page_limit_reached` below says so out loud rather than
# silently returning a truncated room, because a truncated roster is a roster
# that has quietly FORGOTTEN people — the under-reporting error the module
# docstring calls the unsafe direction.
MAX_MEMBERS_PER_PAGE = 1000


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
        if not guild_id or not _channel_id:
            # Not the documented shape. Unavailable, not empty: a malformed id
            # is a wiring fault, and answering "nobody is here" would hide it.
            log.warning(
                "conversation_id %r is not '<guild_id>/<channel_id>'; roster unavailable",
                conversation_id,
            )
            return Roster(available=False)

        headers = {"Authorization": f"Bot {self._token}"}
        try:
            async with (
                aiohttp.ClientSession(timeout=self._timeout) as session,
                session.get(
                    f"{self._base_url}/guilds/{guild_id}/members",
                    headers=headers,
                    params={"limit": str(MAX_MEMBERS_PER_PAGE)},
                ) as resp,
            ):
                if resp.status == 403:
                    log.error(
                        "roster read forbidden for guild %s — the GUILD_MEMBERS "
                        "privileged intent is probably off for this application; "
                        "room-scoped tools stay unavailable until it is enabled",
                        guild_id,
                    )
                    return Roster(available=False)
                if resp.status != 200:
                    log.warning(
                        "roster read for guild %s returned HTTP %s", guild_id, resp.status
                    )
                    return Roster(available=False)
                body = await resp.json()
        except Exception:
            # Never raises — see the module docstring.
            log.warning("roster read failed for guild %s", guild_id, exc_info=True)
            return Roster(available=False)

        if not isinstance(body, list):
            log.warning("roster read for guild %s returned a non-list body", guild_id)
            return Roster(available=False)

        if len(body) >= MAX_MEMBERS_PER_PAGE:
            # A truncated roster silently FORGETS people, which is the
            # under-reporting error this module must never make. Refuse the
            # whole read rather than hand back a partial room.
            log.error(
                "guild %s has at least %d members — this roster does not paginate, "
                "so it would under-report the room; answering unavailable instead",
                guild_id, MAX_MEMBERS_PER_PAGE,
            )
            return Roster(available=False)

        members: list[frozenset[str]] = []
        skipped = 0
        for entry in body:
            user = entry.get("user") if isinstance(entry, dict) else None
            user_id = user.get("id") if isinstance(user, dict) else None
            if not isinstance(user_id, str) or not user_id:
                # UNDER-REPORTING, which this module's own docstring calls the
                # unsafe direction — a roster that has quietly forgotten
                # somebody is one the disclosure gate will happily approve. So
                # the one branch that can drop a real member is counted and
                # reported below rather than
                # refusing the whole read: an entry with no usable id is a
                # shape Discord should never send, and losing the room over one
                # malformed row would be worse than saying so loudly.
                skipped += 1
                continue
            # ONE id form per member, and it is the immutable snowflake. Signal
            # pairs several forms per account because the same person reaches
            # Nora as a phone number on one message and an ACI on another;
            # Discord has no such split. The username is deliberately NOT
            # included — it is not a matchable identity on any platform
            # (`nora.domain.identity`), because its owner can change it.
            members.append(frozenset({user_id}))

        if skipped:
            log.error(
                "roster for guild %s skipped %d member row(s) with no usable id "
                "— the room is UNDER-reported by that many, which is the "
                "direction that can let a disclosure through",
                guild_id, skipped,
            )
        return Roster(available=True, members=tuple(members))
