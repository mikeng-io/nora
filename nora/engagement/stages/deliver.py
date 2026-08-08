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
is counted as `nora_silent_failures_total{reason="no_send_call"}` — unless the
agent loop was cut off by one of R-8's caps, in which case the label names
that cap instead (R-23, see the function's own docstring); a delivered reply
marks `llm_state.last_success_at`.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

from nora.agent.guards import Deliverer, ReplyGuard
from nora.doctrine.loader import Doctrine
from nora.domain.models import ConversationTarget

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
    doctrine: Doctrine,
    canary: str,
    tag_id: str,
    nora_sender_id: str,
    conversation_id: str,
    budgets: Mapping[str, int],
    terminal: Collection[str],
    metrics: Any,
    audience: str,
) -> DeliveryHandle:
    """Build the pair `invoke` needs.

    `audience` labels the `Deliverer`'s five `nora_silent_failures_total`
    branches with which path is speaking — `"tag"` here, `"heartbeat"` from
    `nora.app.fire.FirePath`. **No default**, at either level: a defaulted
    `"tag"` would let the fire path be wired without it and report its failures
    as conversation failures, inside the very budget A8 filters to
    `audience="tag"` (`docs/runbook.md`). That is invisible; a missing keyword
    is a `TypeError`.
    """
    deliverer = Deliverer(
        store=store, adapter=adapter, target=target, doctrine=doctrine, canary=canary,
        tag_id=tag_id, nora_sender_id=nora_sender_id,
        # THE MESSAGE CAP, and there is only one of it (DEC-32, DEC-42): the
        # `Deliverer`'s latch and `ReplyGuard`'s R-7 budget are the SAME number,
        # read here from the same mapping, so they cannot drift. `budgets`
        # is indexed, not `.get`-ed with a fallback — a registry with no
        # `send_reply` is a wiring fault, and a default would let it start and
        # then bound her replies by a number nobody chose.
        message_cap=budgets["send_reply"],
        audience=audience,
        metrics=metrics,
    )
    # `budgets` and `terminal` travel together and come from the same place
    # (`ToolRegistry`) — what a tool costs and whether calling it ends the turn
    # are both properties of the tool, never of the guard (DEC-10, DEC-32).
    guard = ReplyGuard(
        conversation_id=conversation_id, budgets=budgets, terminal=terminal,
        deliverer=deliverer, metrics=metrics,
    )
    return DeliveryHandle(deliverer=deliverer, guard=guard)


# The three `stop_reason` values that mean R-8's caps ended the turn, rather
# than the model doing so. Taken from `strands.types.event_loop.StopReason`,
# copied rather than imported because `nora.engagement` may not import
# `strands` (`tests/test_boundaries.py::test_strands_import_boundary`).
BUDGET_STOP_REASONS = frozenset({
    "limit_turns", "limit_total_tokens", "limit_output_tokens",
})


def truncation_reason(*, stop_reason: str | None, completed: bool) -> str | None:
    """Why a turn that had ALREADY sent something stopped — or None if it finished.

    The observable DEC-42 adds, and the reason it is needed: multi-message's
    own failure mode is a turn cut off between messages, and that turn looks
    successful everywhere else. `deliverer.delivered` is True, `check_outcome`
    returns True, the indicator resolves ✅, the memory feed runs, and
    `nora_silent_failures_total` stays flat — R-23 only relabels turns that
    said NOTHING. The group reads half a thought and no series moves.

    Callers pass this only when at least one message went out; the count is
    the caller's to know (`EngagementPipeline`'s `finally` reads it off the
    `Deliverer`, which is the one place every exit path converges).

    `completed` is "the pipeline reached the end of its try block". Both
    arguments are needed because they answer different halves: `stop_reason`
    covers a loop Strands cut off and returned NORMALLY from — the case with
    no exception, no log and no warning anywhere in the SDK — while
    `completed` covers the ones that never got that far. `stop_reason is None`
    means the invocation itself did not return (`nora.engagement.stages.invoke`
    documents that exactly), which is a timeout or an `llm_error`; both already
    name their own cause on `nora_silent_failures_total`, and this counter adds
    the fact that messages had already gone out when it happened.
    """
    if stop_reason in BUDGET_STOP_REASONS:
        return stop_reason
    if completed:
        return None
    return "invocation_failed" if stop_reason is None else "pipeline_error"


def check_outcome(
    *,
    deliverer: Deliverer,
    tag_id: str,
    platform: str,
    metrics: Any,
    stop_reason: str | None,
    llm_state: Any = None,
) -> bool:
    """Return True iff a reply was actually delivered.

    A False return with `deliverer.attempted` also False means nothing was
    ever sent — counted separately from every other silent-failure reason
    `Deliverer.deliver` itself already counts (empty reply, leak, audit
    failure, send failure), which all imply `attempted`.

    **`stop_reason` is what decides WHICH reason that is, and it is a required
    argument on purpose** (R-23). It used to be unconditionally
    `no_send_call`, and there are two very different turns behind that label:
    a model that had every chance and chose not to speak, and a loop that
    Strands terminated on one of R-8's caps — which it does by returning
    normally, with no exception, no log and no warning anywhere in the SDK.
    Both produced `nora_silent_failures_total{reason="no_send_call"}` and a
    ⚠️, byte for byte, so the most likely cause of a silent turn looked
    exactly like the most benign one. DEC-25 already established the
    principle on the retry path: a `reason` label naming the wrong cause is
    worse than a missing one, because it aims the next investigation at the
    wrong thing. No default value, so a new call site cannot quietly go back
    to always saying `no_send_call`.

    **`audience="tag"` is a literal here because this function is the TAG
    path's, exclusively** (design spec §6.2). The heartbeat's fire path does
    its own outcome accounting — it has its own timeout and its own §8.1
    outcome mapping to produce, and a shared `check_outcome` could give it
    neither. If a second caller ever appears, this literal is wrong and the
    parameter it needs is `audience`, threaded like `Deliverer`'s.
    """
    if not deliverer.delivered:
        if not deliverer.attempted:
            reason = stop_reason if stop_reason in BUDGET_STOP_REASONS else "no_send_call"
            log.warning("%s (tag=%s); dropping the model's text", reason, tag_id)
            metrics.inc(
                "nora_silent_failures_total", reason=reason, platform=platform,
                audience="tag",
            )
        return False
    if llm_state is not None:   # a delivered reply implies a successful invocation
        llm_state.last_success_at = time.time()
    return True
