"""Nora's own Discord identity, via `GET /users/@me`.

Unlike Signal (`nora.platform.signal.self_id`), this is one REST call with no
ambiguity to enrich around: the endpoint returns the bot's own id and
username directly, with no daemon that might answer a different question for
a different id form. No degradation/metrics story is needed the way R-17
requires for Signal's multi-call, best-effort derivation.

The same response also carries the display handle `<presence>` wants:
`username` and `discriminator`. `derive_identity`
reads both off the ONE call it already makes and hands back a rendered handle
alongside the identifiers — `DiscordAdapter.resolve_identity()` (memoized,
called once at startup/listen) is where that handle is cached, and
`own_handle()` reuses it rather than issuing a second `/users/@me` per turn
the way Instagram's live read does. The username is display text ONLY: it is
never one of the returned `Identifier`s (the same rule as every other
platform, `nora.domain.identity`), never added to `self_aliases()`, and never
matched against a sender.
"""

from __future__ import annotations

import aiohttp

from ...domain.identity import Identifier
from .send import DEFAULT_API_BASE


class DiscordIdentityError(Exception):
    """`GET /users/@me` failed or returned a body with no usable id."""


def render_handle(username: object, discriminator: object) -> str | None:
    """`@username#discriminator`, or `@username` alone once Discord has
    migrated the account to unique usernames (`discriminator == "0"`, the
    value every migrated account reports). `None` when there is no usable username.

    Pure formatting: no I/O, so it is exercised directly against the shapes
    `/users/@me` can return without a fake HTTP server.
    """
    if not isinstance(username, str) or not username.strip():
        return None
    name = username.strip()
    tag = discriminator.strip() if isinstance(discriminator, str) else ""
    if tag and tag != "0":
        return f"@{name}#{tag}"
    return f"@{name}"


async def derive_identity(
    token: str,
    *,
    base_url: str = DEFAULT_API_BASE,
    timeout: float = 5.0,  # noqa: ASYNC109 -- caller budget; asyncio.timeout is Phase 6
) -> tuple[str, frozenset[Identifier], str | None]:
    """The bot's own (user id, typed identifiers, display handle):
    `("<snowflake>", {...}, "@name#tag" | "@name" | None)`.

    The handle is read off this SAME response, never a second call — see the
    module docstring.
    """
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
    # change who Nora is. It is not a matchable kind on
    # any platform, so `Identifier` would refuse it anyway.
    identifiers = {Identifier("discord", "user_id", user_id)}
    handle = render_handle(body.get("username"), body.get("discriminator"))
    return user_id, frozenset(identifiers), handle
