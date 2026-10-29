"""Read-only Discord REST directory for the operator control plane.

`nora groups` is a separate process with no Gateway connection, so the
channel set a server entry expands on is read here the way
`nora.platform.signal.directory` reads signal-cli: one plain REST call per
guild, never raised — an unreachable Discord is a `DirectoryError` the
report prints honestly rather than a traceback.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import aiohttp

DEFAULT_API_BASE = "https://discord.com/api/v10"


class DirectoryError(Exception):
    """Discord could not be read; the report says so rather than guessing."""


@dataclass(frozen=True)
class DiscordGuild:
    id: str
    name: str


@dataclass(frozen=True)
class DiscordChannel:
    id: str
    name: str
    type: int                      # discord.py ChannelType's wire value
    parent_id: str | None = None   # set on a thread: the channel it belongs to


async def _get(url: str, *, token: str) -> object:
    try:
        async with (
            asyncio.timeout(8),
            aiohttp.ClientSession() as session,
            session.get(url, headers={"Authorization": f"Bot {token}"}) as resp,
        ):
            if resp.status != 200:
                raise DirectoryError(f"GET {url} answered HTTP {resp.status}")
            return await resp.json()
    except DirectoryError:
        raise
    except Exception as exc:
        raise DirectoryError(f"GET {url} failed: {type(exc).__name__}") from exc


async def list_guilds(*, token: str, base_url: str = DEFAULT_API_BASE) -> list[DiscordGuild]:
    """Every guild the bot is in, so the report can mark the ones no
    `[[conversations]]` entry covers."""
    body = await _get(
        f"{base_url.rstrip('/')}/users/@me/guilds",
        token=token,
    )
    if not isinstance(body, list):
        raise DirectoryError("GET /users/@me/guilds returned a non-list body")
    return [
        DiscordGuild(id=str(g["id"]), name=str(g.get("name") or ""))
        for g in body if isinstance(g, dict) and g.get("id") is not None
    ]


async def list_channels(
    *, token: str, guild_id: str, base_url: str = DEFAULT_API_BASE,
) -> list[DiscordChannel]:
    """Every channel AND thread Discord reports for one guild — threads
    carry `parent_id`, which is how the report marks them as belonging to
    their parent channel's room rather than rooms of their own."""
    body = await _get(
        f"{base_url.rstrip('/')}/guilds/{guild_id}/channels",
        token=token,
    )
    if not isinstance(body, list):
        raise DirectoryError(f"GET /guilds/{guild_id}/channels returned a non-list body")
    out: list[DiscordChannel] = []
    for ch in body:
        if not isinstance(ch, dict) or ch.get("id") is None:
            continue
        parent = ch.get("parent_id")
        out.append(DiscordChannel(
            id=str(ch["id"]), name=str(ch.get("name") or ""),
            type=int(ch.get("type", 0)),
            parent_id=str(parent) if parent is not None else None,
        ))
    return out
