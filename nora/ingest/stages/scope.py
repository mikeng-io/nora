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

A DIRECT MESSAGE is in scope only from somebody in `identity.toml` (Nora never
receives a message from a person who is not in the file). A 1:1 chat's only
member besides her is its sender, so "is this room allowed" and "is this person
known" are the same question there, and it is asked before the audit write for
the room's reason: an unknown sender's words are not kept at all.

A denial is COUNTED. A silently-denied Nora is indistinguishable from a
working one under R-5, and "she stopped answering in that group" needs to be
answerable without attaching a debugger.
"""

from __future__ import annotations

import logging
from typing import Any

from nora.domain.models import NormalizedMessage
from nora.persons import Persons
from nora.policy import Policy

log = logging.getLogger("nora.ingest.scope")


async def run(
    *, message: NormalizedMessage, policy: Policy, persons: Persons, metrics: Any,
) -> bool:
    """Return True if this conversation is allowlisted and ingest may continue."""
    if (
        message.conversation_type == "dm"
        and persons.resolve(message.platform, message.sender_id) is None
    ):
        metrics.inc("nora_denied_conversations_total", platform=message.platform)
        # No sender id in the line: it is somebody's number, and they are
        # nobody Nora knows.
        log.warning(
            "direct message from somebody not in identity.toml (platform=%s) — "
            "not recorded, not answered", message.platform,
        )
        return False
    if policy.conversation(message.platform, message.conversation_id) is not None:
        return True
    metrics.inc("nora_denied_conversations_total", platform=message.platform)
    # A 1:1 chat's id is the person's number, so it is named "dm" here: the
    # operator admits a known person's DM by the id identity.toml already holds.
    where = "dm" if message.conversation_type == "dm" else message.conversation_id
    log.warning(
        "message from a conversation that is not in policy.toml "
        "(platform=%s conversation_id=%s) — not recorded, not answered. "
        "Run `nora groups` to see its name beside this id.",
        message.platform,
        where,
    )
    return False
