"""Discord REST reactions: `PUT`/`DELETE` on a message's `@me` reaction.

Mirrors `nora.platform.signal.react` deliberately — same "log and return,
never raise" contract, same `ReactionResult` vocabulary — because
`nora.engagement.stages.indicate` treats every adapter identically and a
reaction that raised would reach a pipeline that has no business handling one
(R-5: decoration failing is never the group's problem).

WHY THIS NEEDS THE SNOWFLAKE. Signal names a message by `(author, timestamp)`;
Discord names it by its own id. `snowflake.timestamp_ms` is one-way — the low
22 bits (worker, process, sequence) are gone — so the Signal pair cannot be
turned back into a Discord id. The real handle travels in
`NormalizedMessage.message_id`. Without an id there is nothing to react to, and
this says so rather than guessing at a message.

THE EMOJI IS PATH DATA, so it is percent-encoded. A unicode emoji goes in raw
(URL-escaped); a CUSTOM guild emoji would need `name:id` form, which v1 does
not use — every emoji `indicate` sends is a plain unicode character.
"""

from __future__ import annotations

import logging
from urllib.parse import quote

import aiohttp

from ...domain.models import ConversationTarget
from ...domain.ports import ReactionResult

log = logging.getLogger("nora.discord.react")

DEFAULT_API_BASE = "https://discord.com/api/v10"

# Discord's JSON error code for "that emoji is not on that message" — the ONE
# 404 that means the retraction already happened. Its siblings (10008 Unknown
# Message, 10003 Unknown Channel) share the status and mean the target could
# not be addressed at all.
_UNKNOWN_EMOJI = 10014


async def _error_code(resp) -> int | None:
    """Discord's `code` from an error body, or None if it cannot be read.

    None is deliberately NOT treated as the benign case by the caller: a 404
    whose body will not parse is a 404 nothing has established the meaning of.
    """
    try:
        body = await resp.json()
    except Exception:                      # noqa: BLE001 — an unreadable body is just unknown
        return None
    return body.get("code") if isinstance(body, dict) else None


class DiscordReactor:
    def __init__(
        self, token: str, *, base_url: str = DEFAULT_API_BASE, timeout: float = 10.0
    ) -> None:
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def react(
        self,
        *,
        target: ConversationTarget,
        emoji: str,
        to_message_id: str,
        remove: bool = False,
    ) -> ReactionResult:
        """Add or remove Nora's own reaction on one message.

        `supported=False` — not `ok=False` — when there is no message id: that
        is the difference between "this platform cannot do this" and "it tried
        and failed", and `indicate` counts them as different statuses so a
        wiring gap never reads as a transient error (R-27's discipline applied
        to decoration).
        """
        if not to_message_id:
            log.debug("no discord message id to react to; skipping")
            return ReactionResult(ok=False, supported=False)

        # conversation_id is "<guild_id>/<channel_id>"; the endpoint addresses
        # the channel alone, exactly as `DiscordSender.send` does.
        channel_id = target.conversation_id.rsplit("/", 1)[-1]
        url = (
            f"{self._base_url}/channels/{channel_id}/messages/{to_message_id}"
            f"/reactions/{quote(emoji, safe='')}/@me"
        )
        headers = {"Authorization": f"Bot {self._token}"}
        method = "DELETE" if remove else "PUT"
        try:
            async with (
                aiohttp.ClientSession(timeout=self._timeout) as session,
                session.request(method, url, headers=headers) as resp,
            ):
                # 204 is the documented success for both verbs.
                if resp.status == 204:
                    return ReactionResult(ok=True, supported=True)
                # A DELETE of a reaction that is not there answers 404, and that
                # IS a success for this caller: `indicate` retracts the 👀 before
                # placing a terminal emoji, including on paths where it never
                # landed, so "it was already gone" is the state it wanted.
                #
                # But the status alone does not say that. Discord returns 404
                # here for at least three different facts and distinguishes them
                # only in the body's `code`: 10014 Unknown Emoji (genuinely
                # already gone), 10008 Unknown Message (deleted, or a wrong
                # `to_message_id`), 10003 Unknown Channel (a wrong channel id).
                # Treating all three as success would report a wrong id as a clean
                # retraction and count it `status="sent"`.
                if remove and resp.status == 404:
                    code = await _error_code(resp)
                    if code == _UNKNOWN_EMOJI:
                        return ReactionResult(ok=True, supported=True)
                    log.warning(
                        "reaction DELETE 404 with code %s (channel=%s, message=%s) "
                        "— the message or channel could not be addressed, which is "
                        "not the same as the reaction already being gone",
                        code, channel_id, to_message_id,
                    )
                    return ReactionResult(ok=False, supported=True)
                # 429 is reported as an ordinary failure, NOT retried:
                # `ReactionResult` carries no retry axis on purpose, because a
                # reaction is decoration whose moment has passed by the time a
                # backoff would elapse. `indicate` counts it and moves on.
                if resp.status == 429:
                    log.warning("reaction rate-limited: HTTP 429 (channel=%s)", channel_id)
                    return ReactionResult(ok=False, supported=True)
                log.warning(
                    "reaction %s returned HTTP %s (channel=%s)", method, resp.status, channel_id
                )
                return ReactionResult(ok=False, supported=True)
        except Exception:
            # Never raises, for the reason in the module docstring.
            log.warning("discord reaction failed (channel=%s)", channel_id, exc_info=True)
            return ReactionResult(ok=False, supported=True)
