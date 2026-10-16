"""signal-cli JSON-RPC `sendReaction`. Returns ReactionResult; never raises.

The wire contract is signal-cli 0.14.7's own, read off the shipped man pages
rather than guessed: `signal-cli-jsonrpc(5)` states that a JSON-RPC method IS
the CLI command name and that "parameter names are provided in camelCase
format instead of the hyphen format on the cli", and `signal-cli(1)`'s
`sendReaction` section lists `--group-id`, `--emoji`, `--target-author`,
`--target-timestamp` and `--remove`. Those camelCase to exactly the five
params below: `groupId`, `emoji`, `targetAuthor`, `targetTimestamp`, `remove`.

**`targetAuthor` takes the id we already have.** `signal-cli(1)`'s opening
section defines a RECIPIENT argument as any one of a phone number, a bare ACI
UUID, or a PNI UUID with a `PNI:` prefix — precisely because "with the
introduction of phone number privacy, the phone number of a recipient is not
always known". `nora.platform.signal.parser` fills `sender_id` from
`source or sourceNumber or sourceUuid`, so a sender with a hidden phone number
arrives as a bare ACI, which is the second accepted RECIPIENT form. No extra
resolution step is needed, and none is attempted here. The one id form that
would NOT work is a PNI presented without its prefix, which the parser cannot
produce (signal-cli reports a PNI in `sourceUuid` already prefixed).

An empty `to_author` is refused outright rather than posted: a reaction
with no target author is not a reaction signal-cli can route, and posting it
would spend a round-trip to earn a 400.

Mirrors `nora.platform.signal.send.SignalSender` deliberately — same session
construction, same status handling, same "log and return, never raise"
contract — because the two are the same transport with a different method
name, and a reader who has read one should not have to re-read the other.
"""

from __future__ import annotations

import logging

import aiohttp

from ...domain.models import ConversationTarget
from ...domain.ports import ReactionResult

log = logging.getLogger("nora.signal.react")


class SignalReactor:
    def __init__(self, base_url: str, *, timeout: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def react(
        self,
        target: ConversationTarget,
        emoji: str,
        *,
        to_author: str,
        to_timestamp: int,
        remove: bool = False,
        request_id: str = "",
    ) -> ReactionResult:
        """Place (or, with `remove=True`, retract) one reaction.

        Every failure path returns `ReactionResult(ok=False)` — `supported` stays
        True, because signal-cli *does* support reactions; this call just did not
        land. That distinction is what lets the caller count a broken daemon
        separately from a platform that never had the feature.
        """
        if target.conversation_type != "group":
            log.error("refusing to react in conversation_type=%r (v2 is groups only)",
                      target.conversation_type)
            return ReactionResult(ok=False)
        if not to_author or not to_timestamp:
            log.error("refusing to react without a target author and timestamp "
                      "(conversation=%s, id=%s)", target.conversation_id, request_id)
            return ReactionResult(ok=False)
        payload = {
            "jsonrpc": "2.0",
            "method": "sendReaction",
            "params": {
                "groupId": target.conversation_id,
                "emoji": emoji,
                "targetAuthor": to_author,
                "targetTimestamp": to_timestamp,
                "remove": remove,
            },
            "id": request_id or "nora",
        }
        try:
            async with (
                aiohttp.ClientSession(timeout=self._timeout) as session,
                session.post(f"{self._base_url}/api/v1/rpc", json=payload) as resp,
            ):
                if resp.status != 200:
                    log.warning("reaction failed: HTTP %s (conversation=%s, id=%s)",
                                resp.status, target.conversation_id, request_id)
                    return ReactionResult(ok=False)
                body = await resp.json()
        except Exception as exc:            # noqa: BLE001 — a reaction is decoration
            log.warning("reaction failed: %s (conversation=%s, id=%s)",
                        type(exc).__name__, target.conversation_id, request_id)
            return ReactionResult(ok=False)
        if isinstance(body, dict) and body.get("error"):
            log.warning("reaction rejected by signal-cli (conversation=%s, id=%s)",
                        target.conversation_id, request_id)
            return ReactionResult(ok=False)
        return ReactionResult(ok=True)
