"""Is this conversation in scope at all? The room half of the grant predicate.

This runs BEFORE the audit write, and that placement is the whole decision.

Two things could have been enforced in one place; they are deliberately split,
because they protect against different things and want different blast radii:

  * THE ROOM — "should Nora be in this conversation at all". Enforced here, so
    a denied conversation is never recorded. Auditing a room the operator has
    not allowed would store message content he never authorised Nora to keep,
    and being added to a group is not something he controls: anyone can do it.
    Deny-by-default would be hollow if the refusal still wrote the messages to
    disk.

  * THE SENDER — "may this person trigger her". Enforced at the engagement
    gate instead, AFTER the audit, because in an allowed room an unrecognised
    sender is exactly what `nora persons pending` needs to see. Refusing to
    record them would make identifying them impossible, which is the opposite
    of what the operator needs.

Discovery does not depend on auditing denied rooms: `nora groups` reads
signal-cli's `listGroups` directly, so a new group's id is still printable
without Nora having stored a word of it.

A denial is COUNTED. A silently-denied Nora is indistinguishable from a
working one under R-5, and "she stopped answering in that group" needs to be
answerable without attaching a debugger.
"""

from __future__ import annotations

import logging
from typing import Any

from nora.domain.models import NormalizedMessage
from nora.policy import Policy

log = logging.getLogger("nora.ingest.scope")


async def run(*, message: NormalizedMessage, policy: Policy, metrics: Any) -> bool:
    """Return True if this conversation is allowlisted and ingest may continue."""
    if policy.conversation(message.platform, message.conversation_id) is not None:
        return True
    metrics.inc("nora_denied_conversations_total", platform=message.platform)
    log.warning(
        "message from a conversation that is not in policy.toml "
        "(platform=%s conversation_id=%s) — not recorded, not answered. "
        "Run `nora groups` to see its name beside this id.",
        message.platform,
        message.conversation_id,
    )
    return False
