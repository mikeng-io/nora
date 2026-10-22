"""whatsapp-bridge reactions: `POST /react`. Never raises.

Mirrors `nora.platform.discord.react`'s shape rather than Signal's, for the
same reason Discord needed its own: the bridge addresses a message by its
stanza id (`message_id`), and `(to_author, to_timestamp)` — Signal's scheme
— cannot reconstruct one. `NormalizedMessage.message_id` is what carries the
real handle from the parser; an EMPTY `to_message_id` is answered
`supported=False, ok=False`, never guessed at — a reaction pointed at
nothing is not a reaction.

The bridge's 404 `{"error": "unknown_message"}` is its recent-message cache
miss: the stanza id is not in the bounded window the bridge keeps for
reaction targets. That is `supported=True, ok=False` — the platform HAS
reactions; this one could not be addressed — and it logs at WARNING rather
than ERROR because an old message aging out of the cache is an ordinary
event, not a broken bridge.

Every other failure is `supported=True, ok=False` at ERROR, and no exception
ever escapes — `nora.engagement.stages.indicate` treats every adapter
identically, and a reaction that raised would reach a pipeline that has no
business handling one (R-5: decoration failing is never the group's
problem).
"""

from __future__ import annotations

import logging

import aiohttp

from ...domain.models import ConversationTarget
from ...domain.ports import ReactionResult
from ...infra.service_auth import service_auth_headers

log = logging.getLogger("nora.whatsapp.react")


class WhatsAppReactor:
    def __init__(self, base_url: str, *, timeout: float = 10.0) -> None:
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
        """Place (or, with `remove=True`, retract) one reaction.

        `supported=False` — not `ok=False` — when there is no message id:
        the difference between "this platform cannot do this" and "it tried
        and failed", which `indicate` counts as different statuses so a
        wiring gap never reads as a transient error.
        """
        if target.conversation_type != "group":
            # Same deny-by-default as send.py — 1:1 rooms are not admitted, and
            # a reaction is a reply-shaped outbound even though it is not a
            # text send. Refuse without an HTTP call.
            log.error("refusing to react in conversation_type=%r (1:1 is "
                      "deny-by-default this phase)", target.conversation_type)
            return ReactionResult(ok=False, supported=False)
        if not to_message_id:
            log.debug("no whatsapp message id to react to; skipping")
            return ReactionResult(ok=False, supported=False)
        payload = {
            "jid": target.conversation_id,
            "message_id": to_message_id,
            "emoji": emoji,
            "remove": remove,
        }
        try:
            async with (
                aiohttp.ClientSession(timeout=self._timeout) as session,
                session.post(
                    f"{self._base_url}/react", json=payload,
                    headers=service_auth_headers(),
                ) as resp,
            ):
                if resp.status == 200:
                    return ReactionResult(ok=True, supported=True)
                if resp.status == 404:
                    log.warning(
                        "reaction target not in the bridge's recent-message cache "
                        "(conversation=%s) — the message predates it or never landed",
                        target.conversation_id,
                    )
                    return ReactionResult(ok=False, supported=True)
                log.error("reaction failed: HTTP %s (conversation=%s)",
                          resp.status, target.conversation_id)
                return ReactionResult(ok=False, supported=True)
        except Exception:
            # Never raises, for the reason in the module docstring.
            log.error("reaction failed (conversation=%s)", target.conversation_id,
                      exc_info=True)
            return ReactionResult(ok=False, supported=True)
