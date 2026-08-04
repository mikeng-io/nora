"""Stage: deliver — wraps `Deliverer` + `ReplyGuard` from `nora.agent.guards`
(spec C.7, R-2/R-4). They stay in `nora.agent`: both are `strands.HookProvider`s,
and moving them into `nora.engagement` would drag `strands` across the
boundary `tests/test_boundaries.py::test_strands_import_boundary` enforces —
`nora.engagement.pipeline`'s own docstring repeats this for the same reason.

Two entry points, not one, because the pair they wrap is needed at two
different points of a tag: `build()` runs BEFORE the model turn (the guard is
a hook the agent needs; the deliverer is what `send_reply` calls into) and
`check_outcome()` runs AFTER it (there is nothing to check until the model
has had its turn).

`check_outcome` owns the post-invocation "did anything actually go out" check
carried over from `handler.py:205-212`: a model that never calls `send_reply`
is counted as `nora_silent_failures_total{reason="no_send_call"}`; a
delivered reply marks `llm_state.last_success_at`.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from nora.agent.guards import Deliverer, ReplyGuard
from nora.domain.models import ConversationTarget
from nora.obs.metrics import METRICS
from nora.persona.loader import Persona

log = logging.getLogger("nora.engagement.deliver")


@dataclass
class DeliveryHandle:
    """The pair the invoke stage needs: `guard` as the agent's hook,
    `deliverer` as what `send_reply` calls into (and what `check_outcome`
    inspects afterwards)."""

    deliverer: Deliverer
    guard: ReplyGuard


def build(
    *,
    store: Any,
    adapter: Any,
    target: ConversationTarget,
    persona: Persona,
    canary: str,
    tag_id: str,
    nora_sender_id: str,
    conversation_id: str,
    metrics: Any = METRICS,
) -> DeliveryHandle:
    deliverer = Deliverer(
        store=store, adapter=adapter, target=target, persona=persona, canary=canary,
        tag_id=tag_id, nora_sender_id=nora_sender_id, metrics=metrics,
    )
    guard = ReplyGuard(conversation_id=conversation_id, deliverer=deliverer, metrics=metrics)
    return DeliveryHandle(deliverer=deliverer, guard=guard)


def check_outcome(
    *,
    deliverer: Deliverer,
    tag_id: str,
    llm_state: Any = None,
    metrics: Any = METRICS,
) -> bool:
    """Return True iff a reply was actually delivered.

    A False return with `deliverer.attempted` also False means the model
    never called `send_reply` at all — counted separately from every other
    silent-failure reason `Deliverer.deliver` itself already counts (empty
    reply, leak, audit failure, send failure), which all imply `attempted`.
    """
    if not deliverer.delivered:
        if not deliverer.attempted:
            log.warning("no_send_call (tag=%s); dropping the model's text", tag_id)
            metrics.inc("nora_silent_failures_total", reason="no_send_call")
        return False
    if llm_state is not None:   # a delivered reply implies a successful invocation
        llm_state.last_success_at = time.time()
    return True
