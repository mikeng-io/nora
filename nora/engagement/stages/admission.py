"""Stage: admission — owns R-9 (debounce), R-10 (in-flight lock), R-12
(self-tag refusal) and R-30 (per-conversation FIFO queue), the rules that
decide, before a model is ever constructed, WHETHER and WHEN a tag runs.

`run()` is a pure decision function — it does no I/O and does not await — and
returns one of four `Admission` verdicts:

    RUN_NOW     no turn is in flight for this conversation; the lock is
                ACQUIRED here (`in_flight.add(key)`) and the tag runs at once.
    QUEUED      a turn IS in flight; the tag takes its place in this
                conversation's FIFO queue and runs when that turn finishes
                (R-30). The PIPELINE does the actual enqueue, because the
                progress reaction (👀) it carries is created after admission.
    QUEUE_FULL  the queue is already at `queue_max` — a flood, not a wait.
                The newest tag is refused and counted
                (`nora_tags_dropped_total{reason="queue_full"}`); no 👀.
    REJECTED    a self-tag (R-12) or a debounced duplicate (R-9); dropped
                silently, no 👀 — neither is a person waiting for an answer.

R-10 OWNERSHIP SPLIT, spelled out because a lock acquired in one module and
released in another is exactly the shape that grows leaks: THIS module ACQUIRES
— it adds `key` to `in_flight`, and ONLY on the RUN_NOW verdict. It is the
PIPELINE's `finally` that RELEASES (discards `key` on every exit path) and then
re-acquires it for the next dequeued tag — see `nora.engagement.pipeline`'s
docstring for the other half. Nothing in this module ever discards from
`in_flight`, and the QUEUED verdict does NOT touch it: the conversation's lock
already belongs to the turn currently running, and stealing it would let two
turns for one conversation reason from a room each is missing the other's reply
(the correctness argument for the queue — see `nora.engagement.queue`).

R-12 checks membership in `identity.aliases`, not equality against a single id
— see `nora.domain.ports`'s module docstring for why: signal-cli emits `source`
or `sourceNumber` and sometimes a UUID for the same account, and Telegram has
both a numeric id and a username, so equality against one form would silently
stop recognizing Nora's own messages the moment a second platform (or a second
id form on this one) is wired in. The caller
(`nora.engagement.pipeline.EngagementPipeline.run`) resolves the identity via
`adapter.resolve_identity()` — memoized after the adapter's first call, and
already forced to succeed once at startup by
`nora.platform.registry.AdapterRegistry.from_config`'s fail-fast — and passes
it in here already resolved, so this stage does no I/O of its own.
"""

from __future__ import annotations

import enum
import logging
from typing import Any

from nora.domain.models import NormalizedMessage
from nora.domain.ports import PlatformIdentity
from nora.tag import Debounce

log = logging.getLogger("nora.engagement.admission")


class Admission(enum.Enum):
    """The four verdicts `run()` returns. See the module docstring."""

    RUN_NOW = "run_now"
    QUEUED = "queued"
    QUEUE_FULL = "queue_full"
    REJECTED = "rejected"


def run(
    *,
    message: NormalizedMessage,
    identity: PlatformIdentity,
    debounce: Debounce,
    in_flight: set[tuple[str, str]],
    queue_depth: int,
    queue_max: int,
    metrics: Any,
) -> Admission:
    """Classify a tag. On `RUN_NOW`, `key` has already been added to
    `in_flight` — the caller (the pipeline) owns releasing it. On `QUEUED`, the
    caller owns the actual enqueue. See the module docstring."""
    if message.sender_id in identity.aliases:               # R-12
        log.info("ignoring a trigger from our own account")
        return Admission.REJECTED

    key = (message.platform, message.conversation_id)
    if not debounce.allow(key):                             # R-9
        log.info("debounced tag in %s", message.conversation_id)
        metrics.inc("nora_tags_debounced_total")
        return Admission.REJECTED

    if key in in_flight:                                    # R-10: a turn is running
        if queue_depth >= queue_max:                       # R-30: flood guard
            log.warning(
                "tag_dropped_queue_full in %s (depth=%d, max=%d)",
                message.conversation_id, queue_depth, queue_max,
            )
            metrics.inc("nora_tags_dropped_total", reason="queue_full")
            return Admission.QUEUE_FULL
        log.info(
            "tag_queued in %s (depth=%d)", message.conversation_id, queue_depth,
        )
        # Counted the same as a run-now tag: a queued tag IS accepted for
        # handling, just later — dequeue does not pass back through here, so
        # this is the one and only place it is counted.
        metrics.inc("nora_tags_total", platform=message.platform)
        metrics.inc("nora_tags_queued_total")
        return Admission.QUEUED

    in_flight.add(key)                                      # R-10: acquire
    metrics.inc("nora_tags_total", platform=message.platform)
    return Admission.RUN_NOW
