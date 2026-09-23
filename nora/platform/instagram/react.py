"""instagram-bridge reactions: `POST /react`
(design/instagram-adoption/03-transport-contract.md). Never raises.

Mirrors `nora.platform.whatsapp.react` rather than Signal's, for the same
reason WhatsApp needed its own: the bridge addresses a message by its platform
message id (`mid.$…`), and `(to_author, to_timestamp)` — Signal's scheme —
cannot reconstruct one. `NormalizedMessage.message_id` is what carries the real
handle from the parser; an EMPTY `to_message_id` is answered
`supported=False, ok=False`, never guessed at — a reaction pointed at nothing
is not a reaction.

**`room_id` on this route is the SHORT thread id, and that is a CORRECTION.**
`03` said until 2026-09-22 that every send takes the long id; measurement says
`/react` addressed with the LONG id is **accepted and never arrives** — a 200
for a reaction nobody sees. Text works with either, which is why the wrong rule
went unnoticed. The contract now exposes only the short id and the bridge owns
the translation, so this module has nothing to choose; the note exists so a
reader who finds an older copy of the document knows which way it was settled.

`remove` rides as its own field, **and the emoji goes WITH it**. `03`'s pinned
request shape is `{room_id, message_id, emoji}` and omits `remove` entirely —
the bridge reads it (`reactRequest.Remove`) and refuses an empty `emoji` even
on a removal with `400 emoji is required (also when remove is true)`, because
Instagram's mutation identifies WHICH reaction is being retracted, not merely
that one is. Written against the route as implemented, with the document's
omission reported rather than worked around: dropping the emoji on a removal
would have been a 400 on every retraction, and dropping `remove` would have
placed a second reaction where one was meant to be taken away.

The bridge has no documented 404 for an unknown message: unlike WhatsApp's
bounded recent-message cache, `/react` is a pass-through to Instagram, which
answers 400 for a malformed request and 500 for a refusal it has no name for.
409 gets its own line for the same reason it does in `send.py` — a checkpoint
is a human's problem, not a retry's — and it stays `supported=True`, because
the platform HAS reactions and this one simply could not be placed.

Every failure is `supported=True, ok=False` and no exception ever escapes —
`nora.engagement.stages.indicate` treats every adapter identically, and a
reaction that raised would reach a pipeline that has no business handling one
(R-5: decoration failing is never the group's problem).
"""

from __future__ import annotations

import logging

import aiohttp

from ...domain.models import ConversationTarget
from ...domain.ports import ReactionResult
from ...infra.trace_propagation import propagation_headers

log = logging.getLogger("nora.instagram.react")


class InstagramReactor:
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

        `supported=False` — not `ok=False` — when there is no message id: the
        difference between "this platform cannot do this" and "it tried and
        failed", which `indicate` counts as different statuses so a wiring gap
        never reads as a transient error.
        """
        if target.conversation_type != "group":
            # Same deny-by-default as send.py — 1:1 rooms are not admitted
            # this phase, and a reaction is a reply-shaped outbound even
            # though it is not a text send. Refuse without an HTTP call.
            log.error("refusing to react in conversation_type=%r (1:1 is "
                      "deny-by-default this phase)", target.conversation_type)
            return ReactionResult(ok=False, supported=False)
        if not to_message_id:
            log.debug("no instagram message id to react to; skipping")
            return ReactionResult(ok=False, supported=False)
        payload = {
            "room_id": target.conversation_id,
            "message_id": to_message_id,
            # The emoji is sent on a REMOVAL too — see the module docstring:
            # the mutation names which reaction is being retracted, and the
            # bridge 400s an empty one.
            "emoji": emoji,
            "remove": remove,
        }
        try:
            async with (
                aiohttp.ClientSession(timeout=self._timeout) as session,
                session.post(
                    f"{self._base_url}/react", json=payload, headers=propagation_headers(),
                ) as resp,
            ):
                if resp.status == 200:
                    return ReactionResult(ok=True, supported=True)
                if resp.status == 409:
                    log.error(
                        "reaction refused: instagram session is checkpointed or logged "
                        "out (conversation=%s) — a human has to clear it",
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
