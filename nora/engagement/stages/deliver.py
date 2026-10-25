"""Stage: deliver — wraps `Deliverer` + `ReplyGuard` from `nora.agent.guards`
(R-2/R-4). They stay in `nora.agent`: both are `strands.HookProvider`s,
and moving them into `nora.engagement` would drag `strands` across the
boundary `tests/test_boundaries.py::test_strands_import_boundary` enforces —
`nora.engagement.pipeline`'s own docstring repeats this for the same reason.

Two entry points, not one, because the pair they wrap is needed at two
different points of a tag: `build()` runs BEFORE the model turn (the guard is
a hook the agent needs; the deliverer is what `send_reply` calls into) and
`check_outcome()` runs AFTER it (there is nothing to check until the model
has had its turn).

`check_outcome` owns the post-invocation "did anything actually go out" check:
a model that never calls `send_reply`
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

from nora.agent.guards import Deliverer, ReplyGuard, ToolCallLog
from nora.agent.links import LinkCarry
from nora.agent.outbound import OutboundComposer
from nora.agent.reasoning_strip import ReasoningStripWatch
from nora.doctrine.loader import Doctrine
from nora.domain.models import ConversationTarget
from nora.obs.tracing import annotate, span

log = logging.getLogger("nora.engagement.deliver")


@dataclass
class DeliveryHandle:
    """What the invoke stage needs: `guard` as the agent's hook, `deliverer` as
    what `send_reply` calls into (and what `check_outcome` inspects afterwards),
    and `links` as the one object the TOOLS and the `Deliverer` must share.

    `links` is on the handle rather than reached for off `deliverer` (DEC-89).
    Both would work; only this one makes the sharing visible at the call site,
    where the caller passes the same object to `ToolRegistry.build(links=...)`
    that it has just given the `Deliverer`. Reaching through
    `handle.deliverer.links` would let a caller wire the tools to a carrier and
    silently forget the sender — the feature present, decided, and inert, which
    is DEC-42's failure and the reason `composer` has no default on `build`."""

    deliverer: Deliverer
    guard: ReplyGuard
    links: LinkCarry
    tool_call_log: ToolCallLog
    # Built HERE for `tool_call_log`'s reason — this is the one
    # seam both production senders pass through
    # (`tests/architecture/test_only_two_paths_can_send.py` proves there are
    # exactly two), so building it here is what makes BOTH paths carry it
    # rather than one. It is also the only place that holds `audience`, which
    # is what the row's `path` column records.
    reasoning_watch: ReasoningStripWatch


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
    composer: OutboundComposer | None,
    forced_first_tool: str | None = None,
    on_delivered: Any = None,
    cacheable_tools: frozenset[str] = frozenset(),
    tool_call_log_store: Any = None,
    reasoning_strip_store: Any = None,
    send_permitted: bool = True,
    lookups: Collection[str] | None = None,
    param_names: Mapping[str, frozenset[str]] | None = None,
    intent: str | None = None,
    content_checks: bool = True,
) -> DeliveryHandle:
    """Build the pair `invoke` needs.

    `audience` labels the `Deliverer`'s five `nora_silent_failures_total`
    branches with which path is speaking — `"tag"` here, `"heartbeat"` from
    `nora.app.fire.FirePath`. **No default**, at either level: a defaulted
    `"tag"` would let the fire path be wired without it and report its failures
    as conversation failures, inside the very budget A8 filters to
    `audience="tag"` (`docs/runbook.md`). That is invisible; a missing keyword
    is a `TypeError`.

    **`composer` has no default here either, and `None` is a legal value** —
    the two statements are not in tension, and the distinction is which
    mistake stays invisible. `Deliverer` defaults it, because a test about R-2
    should not have to care; THIS function is the seam both production senders
    pass through (`tests/architecture/test_only_two_paths_can_send.py` proves
    there are exactly two), so requiring the keyword is what stops the feature
    from shipping INERT — wired everywhere, decided nowhere, silently sending
    plain text while a decision index entry claims otherwise (DEC-42). Passing `None` is a
    decision (the offline and eval compositions genuinely have no roster and no
    preview fetcher); omitting the argument is a `TypeError`.

    `send_permitted` defaults True and only the participation occasion ever
    passes False (its SEND cooldown). It is
    deliberately NOT required like `audience`/`composer`: a default here has no
    wrong claim available to it (True is the behaviour every pre-participation
    caller already has), whereas those two would get a missing value WRONG while
    looking right.

    `lookups` arms R-97 and names the tools it watches
    (`ToolRegistry.lookup_tools()`); `None`, the default, is the rule OFF. It
    takes the same shape as `send_permitted` and for the same reason: `None` is
    the behaviour of every caller but one, and the ONE caller
    that arms it is the participation occasion — where nobody asked, so a reply
    reporting that a lookup found nothing is noise rather than an answer owed.
    The tag path leaves it off deliberately: there she was called, and «I looked
    and there is nothing» is exactly what she owes the person who asked.

    `param_names` arms R-99: each tool's own accepted argument names, from `ToolRegistry.tool_param_
    names()`. `None`, the default, is the rule OFF — the shape `lookups` takes
    above, for the same reason: a caller with no `ToolRegistry` in scope (a
    bench test) gets a guard that cannot check this axis, not one that rejects
    every call it sees.

    """
    # DEC-89: one carrier per turn, built HERE because this is the only place
    # both users of it are in scope — the `Deliverer` below, and (via the
    # returned handle) the `ToolRegistry.build(links=...)` call the caller makes
    # next. `deliver.build` has exactly two production callers
    # (`tests/architecture/test_only_two_paths_can_send.py` proves it), so
    # minting it here is what makes both paths carry links rather than one.
    links = LinkCarry()
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
        # DEC-83: who to tell the moment a reply lands, so the ✅ does not wait
        # for the agent loop's last (discarded) model call. Optional and
        # defaulting to None — the fire path passes nothing, and a turn with no
        # observer delivers without notifying anyone.
        on_delivered=on_delivered,
        composer=composer,
        links=links,
        send_permitted=send_permitted,
        intent=intent,
        content_checks=content_checks,
    )
    # `budgets` and `terminal` travel together and come from the same place
    # (`ToolRegistry`) — what a tool costs and whether calling it ends the turn
    # are both properties of the tool, never of the guard (DEC-10, DEC-32).
    guard = ReplyGuard(
        conversation_id=conversation_id, budgets=budgets, terminal=terminal,
        deliverer=deliverer, metrics=metrics, forced_first_tool=forced_first_tool,
        lookups=lookups, param_names=param_names,
    )
    tool_call_log = ToolCallLog(
        conversation_id=conversation_id, conversation_type=target.conversation_type,
        tag_id=tag_id, cacheable=cacheable_tools, store=tool_call_log_store,
        metrics=metrics,
    )
    # `path=audience` rather than a literal or a second keyword: the two
    # production callers already declare which loop they are
    # (`"tag"` / `"participation"`), it is already required with no default for
    # the reason a second spelling of the same fact would undo, and a row
    # labelled with a different vocabulary than `nora_silent_failures_total`
    # could not be read beside it. `store=None` leaves the watch counting in
    # Prometheus and writing no row.
    reasoning_watch = ReasoningStripWatch(
        path=audience, tag_id=tag_id, store=reasoning_strip_store, metrics=metrics,
    )
    return DeliveryHandle(
        deliverer=deliverer, guard=guard, links=links, tool_call_log=tool_call_log,
        reasoning_watch=reasoning_watch,
    )


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
    audience: str,
    llm_state: Any = None,
) -> bool:
    """Return True iff a reply was actually delivered.

    A False return with `deliverer.attempted` also False means nothing was
    ever sent — counted separately from every other silent-failure reason
    `Deliverer.deliver` itself already counts (empty reply, leak, audit
    failure, send failure), which all imply `attempted`.

    **`stop_reason` is what decides WHICH reason that is, and it is a required
    argument on purpose** (R-23). There are two very different turns behind
    the `no_send_call` label:
    a model that had every chance and chose not to speak, and a loop that
    Strands terminated on one of R-8's caps — which it does by returning
    normally, with no exception, no log and no warning anywhere in the SDK.
    Both would produce `nora_silent_failures_total{reason="no_send_call"}` and a
    ⚠️, byte for byte, so the most likely cause of a silent turn would look
    exactly like the most benign one. DEC-25 already established the
    principle on the retry path: a `reason` label naming the wrong cause is
    worse than a missing one, because it aims the next investigation at the
    wrong thing. No default value, so a new call site cannot quietly always
    say `no_send_call`.

    **`audience` is a required keyword, no default**, like `Deliverer`'s and
    `invoke.run`'s. The
    heartbeat's fire path still does its own outcome accounting (its own
    timeout, its own §8.1 outcome mapping), but the participation occasion
    threads "participation" through here, and a
    defaulted "tag" would land its failures inside the very budget A8 filters
    to `audience="tag"` (`docs/runbook.md`).
    """
    # THE STRICT-SILENCE BOUNDARY. "She chose not to speak" (R-4/R-5: an empty
    # reply, the participation cooldown holding it, or a model that never
    # called `send_reply` at all) and "the send failed" (bytes that passed
    # every guard and the platform rejected) must be distinguishable in the
    # trace. `deliverer.last_outcome` (`nora.agent.guards.Deliverer`) carries
    # the distinction — it is the one field `Deliverer.deliver` writes on every
    # exit — so this reuses it rather than re-deriving a second reading of the
    # same turn: `""`
    # (never called) / `empty_reply` / `cooldown_held` read as HER choice;
    # `cap_spent` / `doctrine_leak` / `egress_block` / `audit_write_failed`
    # read as THIS PROCESS refusing to try; `send_failed` reads as the
    # PLATFORM; `sent` reads as delivered.
    with span(
        "engagement.deliver",
        **{"nora.tag_id": tag_id, "nora.platform": platform, "nora.audience": audience},
    ):
        delivered = deliverer.delivered
        if not delivered:
            # `called`, not `attempted`. `attempted`
            # asks whether a message reached the platform, so a turn whose every
            # reply was refused by R-4/R-6/R-18 answers False and would get
            # `no_send_call` ON TOP of the block already counted for it — two
            # silent-failure increments for one turn, the second of them false. She
            # did call the tool; a guard stopped her, and that is a different
            # failure with a different fix. This also makes `no_send_call` mean what
            # ADR-0006's open investigation reads it as meaning.
            if not deliverer.called:
                reason = stop_reason if stop_reason in BUDGET_STOP_REASONS else "no_send_call"
                log.warning("%s (tag=%s); dropping the model's text", reason, tag_id)
                metrics.inc(
                    "nora_silent_failures_total", reason=reason, platform=platform,
                    audience=audience,
                )
        elif llm_state is not None:  # a delivered reply implies a successful invocation
            llm_state.last_success_at = time.time()
        annotate(**{
            "nora.delivered": delivered,
            "nora.outcome": deliverer.last_outcome or "no_send_call",
            "nora.called": deliverer.called,
            "nora.attempted": deliverer.attempted,
        })
        return delivered
