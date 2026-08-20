"""Turn the model's plaintext `@Name` into a real Discord `<@id>` ping.

The model only ever writes text — `send_reply(text: str)` has no structured
mention parameter, and the transcript it reads never carries a snowflake
(`nora.platform.discord.parser._resolve_inbound_mentions` is the inbound half
of this same problem, and the reason: the model has no id to write even if
it wanted to). So THIS is the only place that can turn `@Miles Novak` into
something Discord will actually notify on: matched, server-side, against the
room's live member list, right before the send.

`DiscordRoster` already reads this same endpoint but deliberately drops
names (`nora/platform/discord/roster.py`'s docstring: a username is not a
matchable identity because its owner can change it, and that roster feeds
the disclosure-safety gate, which never needs a name). This is a SEPARATE,
mention-resolution-only read — never used for disclosure decisions — so
`DiscordRoster`'s contract is untouched.

FAIL-CLOSED, NEVER RAISES, NEVER GUESSES (R-5's shape):
- an unreachable/forbidden/malformed member read yields `{}` — the caller
  sends the model's original text unresolved, exactly today's degraded
  behaviour, never an exception mid-send;
- two members sharing one display name are BOTH dropped rather than either
  one being picked at random — an ambiguous ping is worse than no ping;
- a name in the text that matches no known member is left as plain text.
"""

from __future__ import annotations

import logging

import aiohttp

log = logging.getLogger("nora.discord.mention_resolve")

DEFAULT_API_BASE = "https://discord.com/api/v10"

# Mirrors `DiscordRoster.MAX_MEMBERS_PER_PAGE` — same endpoint, same
# single-page limit. A guild past this page would need pagination this
# module does not implement; unlike the disclosure-safety roster, silently
# resolving against a partial member list is not unsafe here (a missed
# member just means their name goes unresolved, same as any unknown name),
# so this reads what fits in one page rather than refusing the whole read.
MAX_MEMBERS_PER_PAGE = 1000


def _member_name(entry: object) -> str | None:
    """`nick` (this guild's override) > `global_name` > `username` — the same
    precedence discord.py's own `Member.display_name` uses client-side; this
    module has to replicate it by hand because it reads the raw REST member
    list, not a `discord.py` object."""
    if not isinstance(entry, dict):
        return None
    user = entry.get("user")
    if not isinstance(user, dict):
        return None
    nick = entry.get("nick")
    name = nick or user.get("global_name") or user.get("username")
    return name if isinstance(name, str) and name else None


def _member_id(entry: dict) -> str | None:
    user = entry.get("user")
    user_id = user.get("id") if isinstance(user, dict) else None
    return user_id if isinstance(user_id, str) and user_id else None


async def fetch_named_members(
    token: str, *, base_url: str, guild_id: str, client_timeout: aiohttp.ClientTimeout,
) -> dict[str, str]:
    """`{display_name: snowflake_id}` for one guild. Never raises — `{}` on
    any failure, mirroring `DiscordRoster.roster`'s contract: the caller is
    mid-send, and a wedged lookup must degrade the send, not take it down."""
    headers = {"Authorization": f"Bot {token}"}
    try:
        async with (
            aiohttp.ClientSession(timeout=client_timeout) as session,
            session.get(
                f"{base_url}/guilds/{guild_id}/members",
                headers=headers,
                params={"limit": str(MAX_MEMBERS_PER_PAGE)},
            ) as resp,
        ):
            if resp.status != 200:
                log.warning(
                    "member read for guild %s returned HTTP %s; mentions unresolved",
                    guild_id, resp.status,
                )
                return {}
            body = await resp.json()
    except Exception:
        log.warning(
            "member read for guild %s failed; mentions unresolved", guild_id, exc_info=True,
        )
        return {}
    if not isinstance(body, list):
        log.warning("member read for guild %s returned a non-list body", guild_id)
        return {}

    by_name: dict[str, list[str]] = {}
    for entry in body:
        name = _member_name(entry)
        member_id = _member_id(entry) if isinstance(entry, dict) else None
        if name is None or member_id is None:
            continue
        by_name.setdefault(name, []).append(member_id)

    resolved: dict[str, str] = {}
    for name, ids in by_name.items():
        if len(ids) == 1:
            resolved[name] = ids[0]
        else:
            log.warning(
                "%d guild members share the display name %r; skipping — an "
                "ambiguous mention is never guessed", len(ids), name,
            )
    return resolved


def resolve_outbound_mentions(
    text: str, named_members: dict[str, str],
) -> tuple[str, tuple[str, ...]]:
    """Replace `@<a known display name>` in `text` with a real `<@id>` ping.

    Longest name first, so `@Miles Novak` resolves whole rather than matching the
    shorter `@Miles` and leaving ` Novak` dangling as plain text. Only names this
    guild's member list actually has are spliced — an unrecognized `@Someone`
    is left exactly as the model wrote it.

    Returns the (possibly rewritten) text and the ids actually spliced — the
    only ids the caller may tell Discord it's allowed to ping
    (`allowed_mentions.users`).
    """
    if not named_members or "@" not in text:
        return text, ()
    spliced: list[str] = []
    for name in sorted(named_members, key=len, reverse=True):
        token = f"@{name}"
        if token in text:
            text = text.replace(token, f"<@{named_members[name]}>")
            spliced.append(named_members[name])
    return text, tuple(spliced)
