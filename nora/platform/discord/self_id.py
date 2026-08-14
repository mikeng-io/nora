"""Nora's own Discord identity, via `GET /users/@me` (design/discord-integration/03).

Unlike Signal (`nora.platform.signal.self_id`), this is one REST call with no
ambiguity to enrich around: the endpoint returns the bot's own id and
username directly, with no daemon that might answer a different question for
a different id form. No degradation/metrics story is needed the way R-17
requires for Signal's multi-call, best-effort derivation.
"""

from __future__ import annotations

import aiohttp

from ...domain.identity import Identifier
from .send import DEFAULT_API_BASE


class DiscordIdentityError(Exception):
    """`GET /users/@me` failed or returned a body with no usable id."""


async def derive_identity(
    token: str,
    *,
    base_url: str = DEFAULT_API_BASE,
    timeout: float = 5.0,  # noqa: ASYNC109 -- caller budget; asyncio.timeout is Phase 6
) -> tuple[str, frozenset[Identifier]]:
    """The bot's own (user id, typed identifiers)."""
    headers = {"Authorization": f"Bot {token}"}
    async with (
        aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session,
        session.get(f"{base_url.rstrip('/')}/users/@me", headers=headers) as resp,
    ):
        if resp.status != 200:
            raise DiscordIdentityError(f"GET /users/@me returned HTTP {resp.status}")
        body = await resp.json()

    user_id = body.get("id") if isinstance(body, dict) else None
    if not isinstance(user_id, str) or not user_id:
        raise DiscordIdentityError("GET /users/@me returned no usable id")

    # The snowflake ALONE. `username` is read back by `/health` and the
    # operator CLI for display, but it is not an identity: a bot owner can
    # rename the application at any time, so matching on it would let a rename
    # change who Nora is (operator, 2026-08-14). It is not a matchable kind on
    # any platform now, so `Identifier` would refuse it anyway.
    identifiers = {Identifier("discord", "user_id", user_id)}
    return user_id, frozenset(identifiers)
