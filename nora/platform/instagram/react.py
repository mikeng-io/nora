"""instagram-bridge reactions:
`PUT /threads/{thread_fbid}/messages/{message_id}/reaction` with
`{emoji, remove}`. Never raises.

The room and the message are the path: the SHORT thread id, and the message id quoted as one
segment (it carries `$`, and nothing promises it never carries `/`). A 200 is
`{"confirmed": bool}`: `false` means the reaction was accepted and the
mutation's own post-state did not show it, though the reaction lands. It is a success either way, so
only the STATUS is read here. The bodies are pinned by
`tests/fixtures/instagram_bridge/reaction_*`, which the bridge generates.

Mirrors `nora.platform.whatsapp.react` rather than Signal's, for the same
reason WhatsApp needed its own: the bridge addresses a message by its platform
message id (`mid.$…`), and `(to_author, to_timestamp)` — Signal's scheme —
cannot reconstruct one. `NormalizedMessage.message_id` is what carries the real
handle from the parser; an EMPTY `to_message_id` is answered
`supported=False, ok=False`, never guessed at — a reaction pointed at nothing
is not a reaction.

**The thread in the path is the SHORT thread id.** A reaction addressed with
the LONG id is accepted and never arrives — a 200 for a reaction nobody sees —
though text works with either. The contract exposes only the short id and the
bridge owns the translation, so this module has nothing to choose.

`remove` rides as its own field, **and the emoji goes WITH it**. The bridge
refuses an empty `emoji` even on a removal (400), because Instagram's mutation
identifies WHICH reaction is being retracted, not merely that one is. That is
also why a removal is `remove: true` on the same PUT rather than a DELETE of
the reaction: a DELETE has no body to name the emoji in. Dropping the emoji on
a removal would be a 400 on every retraction, and dropping `remove` would place
a second reaction where one was meant to be taken away.

The bridge has no documented 404 for an unknown message: unlike WhatsApp's
bounded recent-message cache, the reaction route is a pass-through to
Instagram. It answers 400 for a malformed request, 502 when Instagram refuses,
and 500 for a failure it has no name for.
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
import urllib.parse

import aiohttp

from ...domain.models import ConversationTarget
from ...domain.ports import ReactionResult
from ...infra.trace_propagation import propagation_headers

log = logging.getLogger("nora.instagram.react")


def reaction_path(conversation_id: str, message_id: str) -> str:
    """`/threads/{thread_fbid}/messages/{message_id}/reaction`. Each id is
    quoted as ONE path segment, so neither can address a different route."""
    room = urllib.parse.quote(conversation_id, safe="")
    message = urllib.parse.quote(message_id, safe="")
    return f"/threads/{room}/messages/{message}/reaction"


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
            # The emoji is sent on a REMOVAL too — see the module docstring:
            # the mutation names which reaction is being retracted, and the
            # bridge 400s an empty one.
            "emoji": emoji,
            "remove": remove,
        }
        try:
            async with (
                aiohttp.ClientSession(timeout=self._timeout) as session,
                session.put(
                    f"{self._base_url}{reaction_path(target.conversation_id, to_message_id)}",
                    json=payload, headers=propagation_headers(),
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
