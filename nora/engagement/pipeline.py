"""`EngagementPipeline` — runs admission → context → doctrine → tools →
invoke → deliver → feedback for a tag `nora.ingest` already decided to
engage. A readable table of contents, not a decision-maker:
every branch belongs to the stage that owns it (`nora.engagement.stages.*`)
or, for doctrine/tools, to the collaborator it delegates straight to
(`DoctrineStore.get`, `nora.agent.registry.ToolRegistry.build`) — neither is
complex enough to earn its own stage module.

**R-10 lock ownership is split across two modules on purpose** — documented
on both halves so a reader landing on either one sees the whole contract.
`nora.engagement.stages.admission.run` ACQUIRES: it acquires `key` in
`ConversationSlots` on admission, and only on admission. THIS module's `run`
RELEASES via its `finally` — but only for a tag admission actually admitted.
`run` below calls `admission.run` OUTSIDE the `try/finally` that owns `key`,
and opens that block only once admission returns True. That ordering matters:
if the lock-release `finally` wrapped the admission call too, a REJECTED tag
(debounced, self-tagged, or dropped because another tag is already in flight
for this conversation) would still release `key` on the way out — for the
in-flight case specifically, that key belongs to a DIFFERENT, still-running
tag, and releasing it would take that other tag's lock out from under it.

The lock and the queue themselves are NOT owned here: they are
`nora.engagement.slots.ConversationSlots`, injected, because the heartbeat
fire path (`nora.app.heartbeat`) has to contend for the same per-conversation
slot. `release_and_dispatch` is deliberately ONE call rather than the two
statements it replaced — a release that forgot to dispatch would strand
whatever queued behind this turn until the next tag arrived, and that tag
would then jump the queue.

The outer `except BaseException` (not `Exception`) is what makes R-5's
silence guarantee unconditional — including on a fault that is not an
`Exception` at all (e.g. `asyncio.CancelledError`). `finally` always runs
regardless of what an `except` clause catches, so `key` is released either
way — narrowing to `except Exception` would not leave it stuck.
What WOULD break: the fault is no longer absorbed — it propagates out of
`run` uncaught (crashing whatever awaits or spawned this tag, an outage far
wider than one tag) — and `nora_silent_failures_total{reason="handler_error"}`
would never be incremented, an observability gap on top of the crash. This
module's test suite probes both.

ONE TRACE PER TAG, AND HOW IT CROSSES THE QUEUE
-----------------------------------------------
A rootless span IS a trace, so the three spans this module opens need a parent
or one tagged message would produce three separate engagement traces plus
ingest's. `run` opens a root, `engagement`, exactly as
`nora.ingest.pipeline.run` does, and `_run_admitted` opens `engagement.turn`
under it with `engagement.invoke` and `engagement.debrief` under that.

**The root cannot simply wrap the work, because the work may not happen here.**
A RUN_NOW tag is awaited inside `run`; a QUEUED tag is enqueued and runs later
on a task `ConversationSlots.release_and_dispatch` spawns. So the tag's context
is CAPTURED at admission (`capture_context`) and re-ATTACHED in `_run_queued`
(`attached`), and the queued turn's spans land in the admission trace — parented
to a root that has already ended, which OTel allows and which renders as the
queue wait it actually was.

**The alternative — letting the task inherit — is not neutral, it is wrong.**
`asyncio.create_task` copies contextvars and OTel's context is a contextvar, so
an un-attached queued tag inherits whatever span was current when it was
spawned. That spawn happens in the PREVIOUS turn's `finally`, inside the
previous turn's own span: the queued tag would be filed under the tag ahead of
it in the queue, asserting a causal relation between two people's messages that
does not exist. `attached` therefore REPLACES
the inherited context rather than adding to it, and a capture that failed
attaches an empty context — a trace of its own, never someone else's.

**Nothing in the span layer may raise**, and that constraint is this module's
rather than the tracer's: `release_and_dispatch` runs in a `finally` PAST the
outer `except BaseException`, so an exception from an observation would skip the
lock release and wedge the conversation forever with no error anywhere.
`nora.obs.tracing`'s `span`, `annotate`, `capture_context` and `attached` are
each guarded on acquisition AND on exit for exactly this call site.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
from collections.abc import Callable, Coroutine, Mapping
from datetime import datetime
from typing import Any

from nora.agent.intent_router import route_intent
from nora.agent.outbound import OutboundComposer
from nora.agent.prefill import (
    build_prefill,
    build_tag_message,
)
from nora.agent.registry import ToolRegistry
from nora.agent.room import Room, trigger_message_ids
from nora.agent.tools import turn_occasion
from nora.domain import audience as audience_names
from nora.domain.culture import CultureRevision
from nora.domain.decision_log import DecisionLogRecorder
from nora.domain.external_files import render_file_links
from nora.domain.ports import (
    EngagementRequest,
    LinkPreviewSource,
    MemoryStore,
    RecordStore,
    RosterSource,
)
from nora.domain.profile import ProfileView
from nora.domain.tag_trace import VALID_OUTCOMES, TagTrace
from nora.domain.tool_call_log import ToolCallLogStore
from nora.domain.turn_record import turn_items
from nora.obs.logging import bind_tag_id, clear_tag_id
from nora.obs.tracing import TRACE_KIND, TraceCarrier, annotate, attached, capture_context, span
from nora.persons import Persons
from nora.policy import Policy
from nora.tag import Debounce

from .mentions import MentionRenderer
from .slots import ConversationSlots
from .stages import admission, debrief, deliver, feedback, indicate, invoke
from .stages.admission import Admission
from .stages.indicate import Indicator
from .stages.invoke import AgentRunner

log = logging.getLogger("nora.engagement.pipeline")

# The one line Nora delivers when the agent loop is cut off by a token/turn cap
# before she could reply (see the NEVER-SILENT FALLBACK below). It is NOT a
# model-authored reply — the one place the strict "she only sends what send_reply
# sent" rule bends, because a visible «I couldn't get to it» beats a void the
# group reads as being ignored. Kept short, generic (the cause varies), Cantonese
# (her register); a non-Cantonese clone would override it. Carries no identifier,
# so it passes the leak/egress scans in `Deliverer.deliver` unchanged.
_EXHAUSTED_FALLBACK = "唔好意思，我一時搞唔掂，遲啲再問我啦 😅"  # noqa: RUF001 — Cantonese fullwidth punctuation is intended

# `nora_engagement_outcomes_total`'s label vocabulary.
# `nora_tagtrace_writes_total{outcome=...}` counts the same values but only
# when the retention store is wired, which answers "did retention get handed a
# trace", not "what did the turn end as"; this one does not ride on the store.
# Derived from `TagTrace.VALID_OUTCOMES`
# itself, never hand-copied, plus the one label this module's `finally`
# produces that `TagTrace` never does: `"unattributed"` is not a seventh
# outcome a completed turn can end in — it is `trace is None`, meaning
# `invoke.run` never returned, or the outer handler deliberately cleared a
# crashed turn's placeholder rather than retain a guessed label.
_ENGAGEMENT_METRIC_OUTCOMES: frozenset[str] = VALID_OUTCOMES | frozenset({"unattributed"})


def _outcome_label(trace: TagTrace | None) -> str:
    """The ONE place `nora.outcome` (the span attribute) and the
    `nora_engagement_outcomes_total` counter read their value from, so the
    two can never disagree about the same turn. RUNTIME check, not just
    mypy: this module is in `make gate`'s mypy scope, but the vocabulary
    constant above is what the metric's help text is written from, and a
    value drifting from it is exactly the label-vocabulary failure CLAUDE.md
    names — cheap insurance against `TagTrace` growing an outcome this
    module forgets to fold into `_ENGAGEMENT_METRIC_OUTCOMES`.
    """
    label = trace.outcome if trace is not None else "unattributed"
    if label not in _ENGAGEMENT_METRIC_OUTCOMES:
        raise ValueError(
            f"nora_engagement_outcomes_total outcome must be one of "
            f"{sorted(_ENGAGEMENT_METRIC_OUTCOMES)}, got {label!r}"
        )
    return label



def _nothing_answered(handle: Any) -> bool:
    """Nothing reached the room, OR only a heads-up did and its answer is still
    owed (`ReplyGuard.reply_still_owed`, DEC-294).

    The three nets below (salvage, the cut-off fallback, DEC-93's recovery)
    gate on this rather than on «nothing delivered»: a heads-up sent with
    `more=true` IS delivered, so gating on delivery alone would drop an answer
    she then wrote as prose, or a turn cut off mid-work, while the turn read as
    `replied`."""
    return (not handle.deliverer.delivered
            or bool(getattr(handle.guard, "reply_still_owed", False)))

class EngagementPipeline:
    def __init__(
        self,
        *,
        adapters: Mapping[str, Any],
        audit: RecordStore,
        memory: MemoryStore,
        persons: Persons,
        doctrine_store: Any,
        context_assembler: Any,
        runner: AgentRunner,
        tool_registry: ToolRegistry,
        policy: Policy,
        debounce: Debounce,
        canary: str,
        display_name: str,
        ai_peer: str,
        context_window: int,
        timeout_seconds: float,
        limits: dict[str, Any],
        defer_tools: bool = False,
        metrics: Any,
        spawn: Callable[[Coroutine[Any, Any, None]], Any],
        slots: ConversationSlots,
        llm_state: Any = None,
        reactions: bool = True,
        rosters: Mapping[str, RosterSource] | None = None,
        preview_source: LinkPreviewSource | None = None,
        tool_call_log_store: ToolCallLogStore | None = None,
        reasoning_strip_store: Any = None,
        tag_trace_store: Any = None,
        debrief_wiring: debrief.Wiring | None = None,
        decisions: DecisionLogRecorder | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        # Platform -> adapter, not a single adapter: a reply must leave on the
        # SAME platform the tag arrived on; selecting one adapter
        # unconditionally would resolve Nora's identity against the wrong
        # platform's aliases (R-12's self-tag check) and deliver the reply into
        # the wrong group. Keyed by `NormalizedMessage.platform`, which is
        # the only thing that can answer "where did this come from".
        self._adapters = adapters
        self._audit = audit
        self._memory = memory
        self._persons = persons
        self._doctrine_store = doctrine_store
        self._context = context_assembler
        self._runner = runner
        self._tool_registry = tool_registry
        # policy.toml's per-conversation MCP allowlist. The ingest pipeline already checked
        # this tag's conversation is admitted at all (nora.ingest.stages.scope,
        # DEC-18) using the SAME Policy instance — this is the second read of
        # it, for a narrower question: which MCP servers, not whether to
        # engage at all.
        self._policy = policy
        self._debounce = debounce
        self._canary = canary
        self._display_name = display_name
        # The Nora aiPeer id (Honcho): her replies feed under it.
        self._ai_peer = ai_peer
        self._context_window = context_window
        # The turn's clock, read for the clock line the way the fire path reads
        # it (`self._clock().astimezone()`), so the line and the `now` tool
        # (which reads the same injected clock) state one instant. `None`
        # falls back to the wall, which is what production's clock is.
        self._clock = clock
        self._timeout_seconds = timeout_seconds
        self._limits = limits
        self._defer_tools = defer_tools
        self._metrics = metrics
        self._spawn = spawn
        self._reactions = reactions
        self._llm_state = llm_state
        # The shared decision log. The tag path is the one decider that is never
        # in doubt about whether it should speak — somebody addressed her — so
        # it READS nothing here and only WRITES, recording what the other
        # deciders could not otherwise see (a tag turn re-deriving an answer
        # participation just sent). `None` is the dormant default; an unwired
        # composition writes nothing.
        self._decisions = decisions
        # Optional, and `None` is a real production-shaped state, not a test
        # affordance: `nora.app.offline` (the integration and eval harnesses)
        # has no daemon to ask. `nora.agent.room.Room` reports an absent source
        # as UNAVAILABLE rather than as an empty room, so a harness — or a
        # wiring mistake — degrades honestly instead of answering "nobody is
        # here" (R-5).
        # Platform -> roster source: each platform's rooms are looked up in its own
        # directory (a Discord conversation id is unknown to signal-cli, which
        # would answer `available=False` and make every room-scoped tool
        # refuse).
        #
        # A MISSING platform here degrades rather than raising, unlike
        # `_adapter_for`: `Room` already documents an absent source as
        # UNAVAILABLE (never as an empty room), so the honest answer is already
        # built, and an offline/eval composition legitimately has no roster at
        # all. Delivering to the wrong platform is catastrophic; failing to
        # read a roster is merely blind, and blind-but-saying-so is allowed.
        self._rosters: Mapping[str, RosterSource] = rosters or {}
        # The OpenGraph fetcher behind an outbound link-preview card. `None`
        # whenever `[preview].enabled` is false and in every offline
        # composition, which is why it defaults rather than being required: a
        # missing preview is a message without a card, and nothing about it is a
        # wrong claim. (`composer` on `deliver.build` is required for the opposite
        # reason; the split is argued there.)
        self._preview_source = preview_source
        # The tool-recall log's write-path store (R-48). `None` in every
        # offline/eval composition and any deployment that has not wired it —
        # `deliver.build`'s `ToolCallLog` treats a `None` store as fully inert,
        # same shape as `roster_source`/`preview_source` above.
        self._tool_call_log_store = tool_call_log_store
        # The durable half of the reasoning-strip watch (`reasoning_strip_log`).
        # Same optional-collaborator shape as `tool_call_log_store` above:
        # `None` leaves `deliver.build`'s `ReasoningStripWatch` counting in
        # Prometheus and writing no row, which is what every offline/eval
        # composition gets.
        self._reasoning_strip_store = reasoning_strip_store
        # The reasoning-continuity retention store. `None` in every offline/eval composition and any
        # deployment that has not wired it — the SAME optional-collaborator
        # shape `preview_source`/`tool_call_log_store` already have above:
        # `_run_admitted`'s `finally` treats a `None` store as fully inert
        # (nothing to save into), never a reason to fail the turn. Production
        # wiring (`composition.py`) is unconditional: retention must exist before
        # the `[debrief]` flag has anything to gate.
        self._tag_trace_store = tag_trace_store
        # The debrief stage's collaborators, as ONE object
        # rather than four optional parameters — see `debrief.Wiring`. `None`
        # is `[standing] enabled = false`, the shipped default, and in every
        # offline/eval composition: `_debrief` below returns without awaiting
        # anything, so a deployment that has not wired it pays nothing and
        # holds the slot for no longer than it otherwise would.
        #
        # Unlike `tag_trace_store` above, this one IS gated on its flag: retention
        # exists unconditionally because a lossy fold is only recoverable from
        # traces that were already being kept, and the fold is the part an
        # operator turns on.
        self._debrief_wiring = debrief_wiring
        # R-10's single-in-flight lock and R-30's per-conversation FIFO queue,
        # both keyed by (platform, conversation_id), as one INJECTED
        # collaborator. The queue feeds the lock's one slot in order as each
        # turn finishes — it never widens it. Both are per-CONVERSATION and
        # self-draining: `release_and_dispatch` on every exit path, and
        # `ConversationQueues.dequeue` deletes a key once its last waiting tag
        # is gone. Neither retains anything per-tag. It is injected rather than
        # constructed here because the heartbeat fire path holds the SAME
        # instance — see `nora.engagement.slots`.
        self._slots = slots

    def _adapter_for(self, platform: str) -> Any:
        """The adapter this tag's platform belongs to, or a hard failure.

        `KeyError` rather than a fallback to any adapter that happens to be
        wired: the fallback IS the bug this mapping exists to prevent — it
        would send a reply to a room on another platform, which is a worse
        outcome than the tag failing loudly (R-5: silence beats a wrong
        answer, and a reply delivered to the wrong group is the wrong answer
        made public). Ingest already admits only platform-qualified
        conversations from `policy.toml` (DEC-18), so reaching this with an
        unknown platform means the registry and the allowlist disagree — a
        wiring fault for an operator, not a condition to paper over.
        """
        try:
            return self._adapters[platform]
        except KeyError:
            raise KeyError(
                f"no platform adapter is wired for {platform!r} — "
                f"wired: {sorted(self._adapters)}"
            ) from None

    async def run(self, request: EngagementRequest) -> None:
        """Classify the tag (admission), then run it now, queue it, or drop it.

        `resolve_identity()` is memoized on the adapter after its first call
        (and is already forced to succeed once, fail-fast, at startup by
        `nora.platform.registry.AdapterRegistry.from_config`), so calling it
        again here is an in-process lookup, not a fresh network round-trip —
        R-12 (`nora.engagement.stages.admission`) needs the resolved identity,
        not the adapter itself, to check self-tag membership.

        The 👀 is fired HERE, at admission, for BOTH the run-now and the queued
        tag — before either handling or waiting begins (R-17/DEC-17 extended by
        DEC-49), not at the turn's start: a person whose tag is queued behind
        two others sees they were SEEN while they wait, so the wait is not
        ambiguous. `indicate.begin` only spawns, so it adds no latency to
        anything. A REJECTED (self-tag/debounce) or QUEUE_FULL (flood) tag gets
        no 👀: neither is a person waiting for an answer, and a 👀 on a dropped
        flood tag would be a progress marker that never resolves — the
        stuck-forever bug the indicator exists to avoid.
        """
        message = request.message
        identity = await self._adapter_for(message.platform).resolve_identity()
        key = (message.platform, message.conversation_id)
        # THE ROOT. Everything this tag opens hangs under it, so one
        # tagged message is one Langfuse trace instead of three — see this
        # module's docstring for why the queued half needs a captured context
        # rather than just a wider `with`. It carries the three facts that make
        # the trace findable without opening a child; a REJECTED tag then leaves
        # a trace that SAYS it was rejected rather than an unexplained stub.
        # `generation` (R-76) — the declaration that keeps this trace. A tag
        # turn is what Langfuse is FOR: `engagement.invoke` wraps the model call
        # and Strands' own generation spans hang under it. It is declared on the
        # ENTRY POINT, not on the run that happened to reach the model: a tag
        # refused at admission never gets there, and its trace is exactly the one
        # DEC-141 exists to preserve — «it SAYS it was rejected» rather than
        # leaving an unexplained stub. Children never re-decide (the sampler is
        # `ParentBased`), so `engagement.admission` and the debrief's generations
        # inherit this and nothing else has to know the rule.
        with span(
            "engagement",
            **{TRACE_KIND: "generation",
               "nora.tag_id": request.tag_id, "nora.platform": message.platform,
               "nora.conversation_id": message.conversation_id},
        ):
            with span("engagement.admission"):
                verdict = admission.run(
                    message=message, identity=identity, debounce=self._debounce,
                    slots=self._slots, metrics=self._metrics,
                )
            annotate(**{"nora.admission": verdict.name})
            if verdict is Admission.REJECTED or verdict is Admission.QUEUE_FULL:
                return
            indicator = self._begin_indicator(request)
            if verdict is Admission.QUEUED:
                # No await between admission's QUEUED verdict and this enqueue,
                # so the depth admission checked cannot change underneath it and
                # FIFO order is exactly arrival order. `identity` is not stored:
                # it is re-resolved (memoized, in-process) when the tag is
                # dequeued.
                #
                # A zero-argument FACTORY, not a coroutine and not a callback:
                # the closure keeps this tag's request and 👀 handle here, in the
                # pipeline, so `ConversationSlots` needs no reference back into
                # it (see that module's docstring). Nothing is created until the
                # slot actually frees, so a queued tag holds no coroutine object
                # either.
                #
                # The trace context rides in that closure alongside them, for
                # the reason the module docstring gives: the factory is invoked
                # from the PREVIOUS turn's `finally`, so the context it would
                # otherwise inherit belongs to a different person's tag.
                carrier = capture_context()
                self._slots.enqueue(
                    key, lambda: self._run_queued(request, indicator, carrier),
                )
                return
            # RUN_NOW. `identity` is threaded on rather than re-resolved: the
            # room-scoped tools need Nora's own id forms to leave herself out of
            # the group's member list, and `PlatformIdentity.aliases` is the
            # same flattened set R-12 tests membership in — the
            # one set that survives a member arriving as a phone number here and
            # an ACI there.
            await self._run_admitted(request, identity, indicator)

    def _begin_indicator(self, request: EngagementRequest) -> Indicator | None:
        """Spawn the 👀 on the tagging message, or None when reactions are off.

        `engagement.reactions.enabled = false` leaves this None, which
        `indicate.resolve` already treats as "nothing to replace" — so the flag
        is one branch here and none in the pipeline's `finally`. A second branch
        down there could drift out of step with this one and strand a 👀.
        `begin` only spawns and cannot raise past its own guard, so the
        `finally` that later reads this handle can never see it unbound."""
        if not self._reactions:
            return None
        return indicate.begin(
            adapter=self._adapter_for(request.message.platform),
            message=request.message, tag_id=request.tag_id,
            metrics=self._metrics, spawn=self._spawn,
        )

    async def _run_queued(
        self, request: EngagementRequest, indicator: Indicator | None,
        carrier: TraceCarrier | None = None,
    ) -> None:
        """Run a tag that waited in the FIFO queue. Its context is assembled
        fresh inside `_run_admitted`, at THIS point — so it sees the replies of
        every tag ahead of it in line, which is the correctness reason the queue
        serialises a conversation (see `nora.engagement.queue`). Identity is
        re-resolved here (memoized) rather than carried through the queue.

        `carrier` is this tag's TRACE context, captured at its own admission and
        re-attached here so its spans join the trace that admitted it. It is the
        one thing that IS carried through the queue, and the module docstring
        says why the alternative — inheriting whatever the dispatching task had
        current — files this turn under the previous person's tag.

        Defaulted to `None`; `attached(None)` starts a fresh trace, which is the
        fail-closed answer rather than a borrowed parent.
        """
        with attached(carrier):
            identity = await self._adapter_for(
                request.message.platform).resolve_identity()
            await self._run_admitted(request, identity, indicator)

    def _profile_name_of(self, person_id: str) -> str:
        """A told-by person id as a NAME for the fold's `<profile>` block
        (45 §5): the identity index's name, "someone" when it has none (R-18 —
        never the id itself)."""
        ref = self._persons.by_person.get(person_id)
        return ref.name if ref is not None else "someone"

    async def _debrief(
        self, *, trace: TagTrace | None, platform: str,
        culture: CultureRevision | None = None,
        profile_room: ProfileView | None = None,
    ) -> None:
        """Hand this finished turn to the debrief stage, or do nothing at all.

        Two guards, and they are different questions rather than one written
        twice. **No wiring** is `[standing] enabled = false` — the shipped
        default and every offline/eval composition — and it must cost nothing:
        this returns without awaiting, so the slot is held for exactly as long
        as it was before the stage existed. **No trace** is the case where
        `invoke.run` never returned at all, or where the outer
        `except BaseException` cleared it deliberately because a crashed turn's
        placeholder outcome would have been a false label. There is nothing to
        review in either, and reviewing a turn under a label that is a guess is
        worse than not reviewing it — the fold would write that guess into a
        row injected into every later turn.

        `now_ms` is stamped HERE rather than inside the stage, following
        `DreamSink.propose`'s reason: the clock belongs to the
        caller that knows when the work happened, and a stage that reads its
        own clock cannot be tested for what it writes without freezing time.

        This is a method rather than inlined in the `finally`, the most crowded
        block in this class, so the two guards above cannot be quietly widened as
        one more `and` on a line of conditions.
        """
        wiring = self._debrief_wiring
        if wiring is None or trace is None:
            return
        # The fold is otherwise untraced: Strands instruments the agent loop, and
        # the debrief's own model call is not in it. Standing
        # writes a row injected into every later turn with no record of the
        # prompt that produced it — counters say a fold took 3s, nothing says
        # what it saw or wrote. Inside the guarded region by construction: this
        # never wraps `release_and_dispatch`, and `span` cannot raise on entry
        # or exit, which matters because this block runs in the `finally` AFTER
        # the outer `except BaseException` has already run.
        with span(
            "engagement.debrief",
            **{"nora.tag_id": trace.tag_id,
               "nora.conversation_id": trace.conversation_id,
               "nora.outcome_reviewed": trace.outcome},
        ):
            await debrief.run(
                trace=trace,
                platform=platform,
                store=wiring.store,
                client=wiring.client,
                objective=wiring.objective,
                metrics=self._metrics,
                display_name=self._display_name,
                record_store=wiring.record_store,
                media_join=wiring.media_join,
                max_chars=wiring.max_chars,
                fold_batch=wiring.fold_batch,
                timeout_seconds=wiring.timeout_seconds,
                store_timeout_seconds=wiring.store_timeout_seconds,
                working_set_max_items=wiring.working_set_max_items,
                working_set_max_chars=wiring.working_set_max_chars,
                content_checks=wiring.content_checks,
                now_ms=int(time.time() * 1000),
                culture=culture,
                profile_room=profile_room,
                profile_name_of=self._profile_name_of,
                conclusions=turn_items(trace.messages, trace.requirements),
            )

    async def _run_admitted(
        self, request: EngagementRequest, identity: Any, indicator: Indicator | None,
    ) -> None:
        """The turn's own span, wrapping the body — the same `run`/`_run` split
        `nora.ingest.pipeline` uses, and for a reason this path has and ingest
        does not.

        The root opened in `run` is already CLOSED by the time a queued tag gets
        here, and an attribute set on an ended span is silently dropped by the
        OTel SDK (it logs and records nothing). So the
        outcome, the stop reason and the latency this turn only learns in its
        `finally` need a span that is still open, and this is it. Opening it
        here rather than widening the root also makes the two admission paths
        render identically: a RUN_NOW turn and a QUEUED turn have the same span
        shape, so the only difference between them in Langfuse is the gap before
        `engagement.turn` starts — which is exactly the queue wait.
        """
        with span(
            "engagement.turn",
            **{"nora.tag_id": request.tag_id,
               "nora.platform": request.message.platform,
               "nora.conversation_id": request.message.conversation_id},
        ):
            await self._run_turn(request, identity, indicator)

    async def _run_turn(
        self, request: EngagementRequest, identity: Any, indicator: Indicator | None,
    ) -> None:
        msg = request.message
        key = (msg.platform, msg.conversation_id)
        started = time.perf_counter()
        llm_elapsed = 0.0
        delivered = False
        # Both bound BEFORE the `try` for the same reason `handle` is: the
        # `finally` reads them on every exit path, including one that faults
        # before the model is ever invoked. `completed` flips at the END of the
        # try block — it is the only thing that distinguishes "the pipeline ran
        # to the end" from "something raised past it", which
        # `deliver.truncation_reason` needs and no stop reason can express.
        stop_reason: str | None = None
        completed = False
        # Bound BEFORE the `try` so the `finally` can read the tag's delivery
        # count on every exit path, including one that faults inside
        # `context.assemble` before there is a handle at all. Reading it off
        # the `Deliverer` rather than tracking a second local is deliberate:
        # a local would be one more thing to remember to increment, and the count
        # can exceed 1 (DEC-40).
        handle: deliver.DeliveryHandle | None = None
        # Bound BEFORE the `try` for the same reason `handle` is: the
        # `finally` saves whatever `invoke.run` handed back on EVERY exit path,
        # including the two (`ok=False`) where `_run_admitted` returns before
        # reaching this method's own `completed = True`. `None` here means
        # `invoke.run` never returned at all (a fault before it was ever
        # called) — the outer `except BaseException` case, which has no
        # `agent.messages` and no `ctx.transcript` window to attribute a
        # trace to, so nothing is saved for it.
        trace: TagTrace | None = None
        # The room's account the turn read at its start (DEC-207), handed to
        # the debrief so the fold reads the SAME account the reply did rather
        # than a second read that could land either side of a fold. Bound
        # here, before the `try`, for `trace`'s reason.
        culture: CultureRevision | None = None
        # The turn's Profile views under the switch (45 §5): the room's one is
        # what the debrief folds against, handed through like `culture`.
        profiles: tuple[ProfileView, ...] = ()
        # Every log line emitted for the rest of this tag — across every stage
        # this method calls into — carries tag_id without any of them naming
        # it explicitly (nora.obs.logging's contextvar bridge).
        bind_tag_id(request.tag_id)
        # `indicator` (the 👀 handle) is created by `run` at ADMISSION time and
        # passed in, so a QUEUED tag's 👀 fires the moment it takes its place in
        # line, not when its turn finally starts — see `run`'s docstring and
        # `_begin_indicator`. It is None when reactions are off, which
        # `indicate.resolve` treats as "nothing to replace".
        try:
            ctx = await self._context.assemble(request=request)
            culture = ctx.culture
            profiles = ctx.profiles
            # An @-mention is a placeholder plus a parallel array; left alone,
            # every name in the transcript — including Nora's own — would reach the
            # model as U+FFFC (DEC-34). Rehydrated HERE rather than at
            # ingest (the audit stores what arrived) or in the prefill (which
            # may not import `nora.platform`), against `raw_json`, which every
            # historical row already carries. See `nora.engagement.mentions`.
            ctx = MentionRenderer(
                persons=self._persons, display_name=self._display_name,
                self_ids=frozenset(getattr(identity, "aliases", ())),
                metrics=self._metrics,
                content_checks=self._tool_registry.content_checks,
            ).rehydrate(ctx)
            doctrine = self._doctrine_store.get(applies_to="tag")
            log.info(
                "tag start (tag_id=%s conversation_id=%s sender_id=%s person_id=%s "
                "doctrine_sha256=%s doctrine_versions=%s)",
                request.tag_id, msg.conversation_id, msg.sender_id,
                ctx.tagger.person_id if ctx.tagger else None,
                doctrine.sha256, doctrine.versions(),
            )

            # `convo is not None` is defensive, not load-bearing: by this
            # point ingest has already proven the conversation is admitted
            # (DEC-18), so `self._policy.conversation(...)` returning `None`
            # here would indicate the ingest and engagement pipelines
            # disagree about which `Policy` they were given — a wiring bug
            # worth defaulting safely on (empty allowlist) rather than
            # crashing the tag over.
            #
            # Computed BEFORE `deliver.build()` and reused for its `budgets=`:
            # `ToolRegistry.budgets()` only folds an MCP tool's budget in when
            # its server name is in `allowed_mcp`, so this pipeline's
            # `ReplyGuard` — and the R-7 unknown-tool veto message it renders —
            # must be built from the SAME set `.build()` below uses, or an
            # unauthorized conversation's guard would still disclose a
            # `pulse_*` name it cannot call.
            convo = self._policy.conversation(msg.platform, msg.conversation_id)
            allowed_mcp = convo.tools if convo is not None else frozenset()
            # DEC-74: the per-conversation location-disclosure trust, sourced the
            # same way `allowed_mcp` is. False (the default) keeps `where_is`'s
            # circle-subset gate strict; a conversation with `location = true`
            # relaxes it for that room only.
            location_trusted = convo.location if convo is not None else False
            handle = deliver.build(
                store=self._audit, adapter=self._adapter_for(msg.platform),
                target=msg.target(),
                doctrine=doctrine, canary=self._canary, tag_id=request.tag_id,
                # Her own id on THIS platform, resolved from the platform
                # itself (see `FirePath._turn` for the full argument).
                # `identity.primary`, NOT `getattr(..., "")`: a default here
                # can only ever hide a defect, and it would write a
                # plausible-looking empty sender onto her own audit rows — in
                # the one place this change argues hardest against guessing at
                # identity. `PlatformIdentity.primary` is required, so an
                # AttributeError here is a real fault worth surfacing.
                nora_sender_id=identity.primary,
                conversation_id=msg.conversation_id,
                budgets=self._tool_registry.budgets(allowed_mcp=allowed_mcp),
                terminal=self._tool_registry.terminal_tools(), metrics=self._metrics,
                param_names=self._tool_registry.tool_param_names(),
                audience=audience_names.TAG,
                cacheable_tools=self._tool_registry.cacheable_tools(),
                tool_call_log_store=self._tool_call_log_store,
                reasoning_strip_store=self._reasoning_strip_store,
                # The outbound decoration stage (@-mentions + a link preview
                # card). ONE per turn, holding the same `roster_source` the
                # room-scoped tools use — so a mention is confirmed against the
                # platform's own membership list for THIS conversation, lazily
                # and at most once, rather than against the whole address book.
                composer=OutboundComposer(
                    platform=msg.platform, conversation_id=msg.conversation_id,
                    persons=self._persons, metrics=self._metrics,
                    roster_source=self._rosters.get(msg.platform),
                    preview_source=self._preview_source,
                    content_checks=self._tool_registry.content_checks,
                ),
                # Routing: a precision-first, abstaining classifier over the
                # message. Non-None only on a narrow high-precision surface (mall
                # opening-hours → place_info) the model misroutes despite the
                # schema; the guard forces it on the first model call and releases.
                forced_first_tool=route_intent(msg.body),
                # DEC-83: settles the 👀 → ✅ the instant a reply is delivered,
                # instead of ~15 s later when the loop's final model call
                # returns. `indicator` is None when reactions are off, and the
                # settler is then inert — the flag stays one branch, in
                # `_begin_indicator`, exactly as its docstring promises.
                on_delivered=indicate.DeliverySettler(
                    indicator=indicator, spawn=self._spawn,
                ),
                content_checks=self._tool_registry.content_checks,
            )
            # One `Room` per tag, constructed here and closed over by the three
            # room-scoped tools. Nothing reads the platform roster unless one
            # of those tools is actually called — the daemon round trip is
            # inside `Room`, lazily, and cached for the tag — so this stays off
            # both the ingest path and the front of the model call.
            room = Room(
                platform=msg.platform, conversation_id=msg.conversation_id,
                roster_source=self._rosters.get(msg.platform), persons=self._persons,
                tagger=ctx.tagger, self_ids=frozenset(getattr(identity, "aliases", ())),
                # From the ASSEMBLED context, not from config: the block is
                # absent when Honcho could not answer as well as when it is
                # switched off, and `about` must do the real lookup in both.
                tagger_card_injected=bool(ctx.tagger_card),
                # The Record row(s) this turn answers: what an event it writes
                # cites (`GatheringService._tag_evidence`), so the withdrawal
                # cascade can reach the event if that message is withdrawn.
                trigger_message_ids=trigger_message_ids(
                    ctx.transcript, sender_id=msg.sender_id, timestamp=msg.timestamp,
                ),
            )
            surface = self._tool_registry.surface(
                # `[llm] defer_tools`.
                defer=self._defer_tools,
                # DEC-256: a spoken turn keeps the tools declared `hot_on`
                # voice resident, so a music request is not a `load_tools`
                # hop of silence in the channel first.
                occasion=turn_occasion(voice=msg.voice),
                store=self._audit, conversation_id=msg.conversation_id,
                conversation_type=msg.conversation_type, persons=self._persons,
                honcho=self._memory, deliverer=handle.deliverer,
                room=room, question=render_file_links(msg.body),
                context_window=self._context_window, metrics=self._metrics,
                # DEC-146: the SAME tuple `build_prefill` below renders
                # `<group_transcript>` from. `who_said` answers out of the window
                # the model is looking at, so it is passed rather than re-read —
                # a second store call here would be a second interpretation of
                # the same messages, and the two would drift the first time one
                # side's limit or naming pass changed.
                # `transcript` is the SHOWN rows: `send_reply(reply_to=n)` and
                # note `[#n]` citations index them. `lookup_window` is the
                # deeper read, for `who_said` ONLY: a lookup must not shrink
                # with the prompt, or «nobody said it» turns into «not in the
                # 20 you were shown».
                transcript=ctx.transcript,
                lookup_window=ctx.lookup_transcript or None,
                allowed_mcp=allowed_mcp, location_trusted=location_trusted,
                # DEC-89: the SAME carrier the `Deliverer` above holds. The
                # tools mint links into it and the sender puts back the ones the
                # reply earned and dropped; two carriers would be a feature that
                # collects perfectly and appends nothing.
                links=handle.links,
            )
            tagger_name = self._persons.display_name(msg)
            # NO ROSTER IN THE PROMPT; there is a tool instead.
            # `identity.toml` is Nora's address book across EVERY room; handing
            # all of it to one room discloses the existence and names of people
            # who are not in it — a real leak, not just a wrong answer.
            # `group_members` answers that question from the PLATFORM's
            # membership list for this conversation, so the scope is the room
            # itself rather than the address book, and it costs a tool call
            # only when somebody actually asks. The tagger's own aliases still
            # reach the model via `build_tag_message`, scoped by construction.
            pre_fill = build_prefill(
                ctx, display_name=self._display_name, context_window=self._context_window,
                catalogue=surface.prompt_catalogue,
                now=self._clock().astimezone() if self._clock is not None else None,
            )
            tag_message = build_tag_message(
                ctx, tagger_name=tagger_name, display_name=self._display_name,
            )

            # `invoke.run` hands back a `TagTrace` with a PROVISIONAL
            # `outcome` — `LiveAgentRunner`'s own docstring calls it out as a
            # placeholder on the `ok=True` path. Bound to the method-level
            # `trace` (not a local `_trace`) so the `finally` can save it on
            # every exit this `try` takes from here on, including the
            # `not ok` early return two lines down — on THAT path the trace
            # already carries the real outcome (`"timeout"`/`"llm_error"`,
            # `invoke.run`'s own `_blank_trace`), so there is nothing to
            # correct.
            with span(
                "engagement.invoke",
                **{"nora.tag_id": request.tag_id,
                   "nora.doctrine_sha256": doctrine.sha256,
                   "nora.note_index_entries": len(ctx.notes)},
            ):
                ok, llm_elapsed, stop_reason, final_text, trace = await invoke.run(
                    runner=self._runner, doctrine=doctrine, tools=surface.tools, pre_fill=pre_fill,
                    guard=handle.guard, tool_call_log=handle.tool_call_log,
                    reasoning_watch=handle.reasoning_watch,
                    tool_hooks=surface.hooks,
                    tag_message=tag_message, limits=surface.limits(self._limits), ctx=ctx,
                    timeout_seconds=self._timeout_seconds, metrics=self._metrics,
                    # This pipeline IS the tag path; the NAME stays at the
                    # call site rather than becoming a default so an omitted
                    # one cannot compile (the A8 filter is `audience="tag"`).
                    # It is `nora.domain.audience.TAG` rather than a literal, because
                    # `reasoning_strip_log.path` reads the same vocabulary.
                    audience=audience_names.TAG,
                )
                # Inside the `with`, so it lands on the invoke span rather than
                # on the turn: `ok` and `stop_reason` are what this stage
                # decided, and the turn's own copy is annotated separately in
                # the `finally` below from the corrected trace.
                annotate(**{"nora.invoke_ok": ok, "nora.stop_reason": stop_reason or ""})
            if not ok:
                # A timeout or an `llm_error`. Messages may ALREADY have gone
                # out — under multi-message that is the common shape, not an
                # edge case — so this is a partial delivery, not a silent turn,
                # and the `finally` counts it as one.
                #
                # DELIBERATELY NO never-silent fallback here: unlike a budget cut-off, this path
                # carries DEC-42 partial-reply accounting (`truncation_reason`),
                # and delivering a fallback would forge a "partial reply" the
                # tests here pin apart. A timeout is ALSO already surfaced as an
                # infra failure on `nora_silent_failures_total{invocation_timeout}`
                # — a real alert, not the silent void the budget fallback closes.
                # Closing this path too would need DEC-42 reconciliation.
                #
                # ONE exception (salvage-partial-reply): a
                # `max_tokens` cut-off is NOT a generic `llm_error`. Strands
                # appends the partial message to `agent.messages` before raising
                # (`nora.agent.factory.invoke` catches it and carries the words
                # up as `PartialReplyError`), so the model WROTE an answer and
                # the harness dropped it. The words are her own, not the
                # SDK's canned recovery notice (factory.invoke strips that), so
                # delivering them is not the forgery the fallback above refuses
                # to commit. `stop_reason == "max_tokens"` is the discriminator:
                # a generic `llm_error` has `stop_reason is None` (invoke.py),
                # so this branch cannot fire on a timeout or an API error.
                # On successful delivery the turn FLOWS THROUGH to
                # `check_outcome` rather than returning here — it read
                # `deliverer.delivered` True, corrected the trace, fed Honcho
                # and marked the turn completed exactly like a normal delivery.
                salvable = (
                    # Occasion-gated: a
                    # participation turn never delivers a draft on ANY branch —
                    # no grounded value is silence there, and a salvaged draft
                    # would speak words she concluded without the tool.
                    request.occasion != "participation"
                    and stop_reason == "max_tokens"
                    and _nothing_answered(handle)
                    and final_text.strip()
                )
                if salvable:
                    if await handle.deliverer.deliver(final_text, source="salvaged"):
                        self._metrics.inc(
                            "nora_salvaged_replies_total", platform=msg.platform,
                        )
                        log.warning(
                            "salvaged a partial reply cut off at max_tokens "
                            "(tag=%s, %d chars)", request.tag_id, len(final_text),
                        )
                        # NO early return and NO `delivered = True` here: the
                        # turn flows through to `check_outcome` below, which
                        # already reads `deliverer.delivered` (True after the
                        # salvage) and corrects the trace, feeds Honcho and
                        # marks the turn completed — the same accounting a
                        # normal delivery gets. An early return here would
                        # skip exactly those three things.
                    else:
                        # Delivery refused (R-2/R-4/R-6/R-18) — nothing went
                        # out, stay silent exactly like the generic paths.
                        return
                else:
                    # Timeout, generic `llm_error`, or a partial with no
                    # usable prose (only Strands' canned recovery notice).
                    # Stay silent (R-5) — this path never delivered anything.
                    return

            # NEVER-SILENT FALLBACK: the loop was cut off by
            # one of R-8's caps (limit_total_tokens/turns/output) BEFORE she sent a
            # reply — she was mid-work, not declining. A weak model that will not
            # stop flailing on a hard query (a specific carpark, a weather page)
            # hits this repeatedly, and to the group silence is indistinguishable
            # from being ignored. So deliver ONE honest line here — BEFORE
            # `check_outcome`, so a RECOVERED turn is not also counted a silent
            # failure. ONLY on a budget cut-off: a plain `end_turn` with no send is
            # a DELIBERATE silence she is allowed (strict-silence, R-4/R-2) and must
            # stay silent. If she already sent something (multi-message cut off
            # mid-stream), `deliverer.delivered` is True and this is skipped — that
            # is a partial delivery, not a void. Except a heads-up whose answer
            # is still owed (`_nothing_answered`): the room was told an answer
            # is coming, and the cut-off means it is not.
            cut_off_before_reply = (
                # Occasion-gated like the salvage above: the participation
                # occasion's exhaustion is silence, never the canned line.
                request.occasion != "participation"
                and stop_reason in deliver.BUDGET_STOP_REASONS
                and _nothing_answered(handle)
            )
            # The local record of WHICH branch below fires:
            # `deliverer.delivered` alone cannot tell "her own words,
            # recovered" apart from "the tool called it" or "the canned
            # fallback" — all three leave it True. Bound here, before either
            # branch, so it is a real `False` on the tool-called-normally path
            # too (neither branch runs at all).
            recovered_delivered = False
            if cut_off_before_reply:
                # PREFER HER OWN WORDS OVER THE CANNED LINE.
                #
                # `final_text` on a budget cut-off is one of two things,
                # and both are hers: prose the cut-off completion happened to
                # leave behind, or — when it left none — the drafting call
                # `nora.app.agent_runner` makes over the context she had
                # already gathered (`factory.draft_from_exhausted_loop`). This
                # branch does not care which; it is the same question either
                # way, "did she end up with something to say".
                #
                # The canned apology stays as the fallback-of-the-fallback,
                # for the turn that genuinely has nothing — a cap that bound
                # on the FIRST call, or a drafting call that timed out. It is
                # never removed: R-23's never-silent guarantee is what this
                # whole branch exists for, and improving the line must not
                # cost the guarantee.
                #
                # `source` distinguishes them for `Deliverer`, and that is not
                # cosmetic: `source="fallback"` is excluded from the Honcho
                # feed because nobody composed it, while a drafted answer IS
                # her own sentence and her memory of her own speech should
                # contain it — exactly the argument DEC-93's recovery makes
                # below for the same reason.
                drafted = final_text.strip()
                if drafted and await handle.deliverer.deliver(
                    drafted, source="drafted"
                ):
                    self._metrics.inc(
                        "nora_budget_drafts_delivered_total", reason=stop_reason,
                    )
                    log.warning(
                        "delivered a draft from an exhausted loop "
                        "(tag=%s, reason=%s, %d chars)",
                        request.tag_id, stop_reason, len(drafted),
                    )
                elif await handle.deliverer.deliver(
                    _EXHAUSTED_FALLBACK, source="fallback"
                ):
                    self._metrics.inc(
                        "nora_loop_exhausted_fallbacks_total", reason=stop_reason,
                    )

            # DEC-93: she WROTE a reply and did not put it through the tool.
            #
            # `tool_choice="required"` is what makes a model reach for `send_reply`
            # instead of answering in prose (`nora.agent.tool_choice`), but a
            # provider may ACCEPT AND IGNORE it, leaving nothing forcing the
            # terminal verb: the group goes quiet with a complete,
            # group-addressed answer sitting in a `text` block.
            #
            # So the words are recovered rather than a canned line sent in their
            # place. Narrow on purpose:
            #   - `end_turn` ONLY. A budget cut-off is handled above and a
            #     timeout produced no considered ending at all.
            #   - nothing delivered, so this can never append to a real reply;
            #     or only a heads-up whose answer is still owed (DEC-294,
            #     `_nothing_answered`), which is the reply it completes.
            #   - `_EXHAUSTED_FALLBACK` is preferred when both could apply, by
            #     ordering: a cut-off turn is not a deliberate ending.
            #   - it goes through `deliver`, so R-4/R-6/R-18, the Markdown
            #     conversion, the link carry and the audit row all apply exactly
            #     as they would have. Nothing is bypassed to rescue it.
            #
            # It DOES feed Honcho, unlike `_EXHAUSTED_FALLBACK`. `Deliverer`
            # excludes only `source="fallback"`, and the distinction is real:
            # the fallback is a canned apology nobody composed, while this is
            # her own sentence that merely took the wrong exit. The group read
            # it; her memory of her own speech should contain it.
            #
            # Counted because the honest read of a non-zero value here is «the
            # provider is not honouring `required`», NOT «this feature is
            # working».
            elif (
                # Occasion-gated like both branches above: on the participation
                # occasion a draft written outside the tool is a determination
                # to stay silent that took the wrong exit, not words to rescue.
                request.occasion != "participation"
                and stop_reason == "end_turn"
                # The text a CANCELLED model call ends with is the guard's veto
                # string, not the model's words. Refusing here is
                # what keeps DEC-93's own rule true — «nothing reaches the group
                # unless she WROTE it» — because that string passes R-6 and R-18
                # trivially and nothing else downstream would stop it.
                and not handle.guard.cancelled_model_call
                and _nothing_answered(handle)
                and final_text.strip()
                and await handle.deliverer.deliver(final_text, source="recovered")
            ):
                recovered_delivered = True
                self._metrics.inc("nora_recovered_replies_total", platform=msg.platform)
                log.warning(
                    "recovered a reply the model wrote WITHOUT calling send_reply "
                    "(tag=%s, %d chars, after_heads_up=%s) — without a heads-up "
                    "the route is not honouring tool_choice=required",
                    request.tag_id, len(final_text),
                    bool(getattr(handle.guard, "reply_still_owed", False)),
                )

            # `stop_reason` is threaded rather than dropped (R-23): without it
            # a turn Strands cut off at one of R-8's caps is indistinguishable
            # here from a model that chose not to speak, and both would count
            # as `no_send_call`.
            delivered = deliver.check_outcome(
                deliverer=handle.deliverer, tag_id=request.tag_id, platform=msg.platform,
                stop_reason=stop_reason, llm_state=self._llm_state, metrics=self._metrics,
                # Literal at the call site, like `invoke.run`'s above: this
                # pipeline IS the tag path (the A8 filter reads this label).
                audience="tag",
            )
            # Correct `trace`'s placeholder `outcome` now that the
            # turn's real disposition is known — mirroring `check_outcome`'s
            # OWN delivered/called split (deliver.py:222-238) rather than
            # inventing a second one. `check_outcome` itself only returns a
            # bool; the finer send_reply/recovered split reuses
            # `recovered_delivered`, the SAME local flag the branch above just
            # set, so this reads off state that already exists rather than
            # re-deriving it a third way. `deliverer.called` — True the moment
            # ANY `deliver()` call was attempted, whether by the tool or by
            # this method's own fallback/recovery attempts — is exactly
            # `check_outcome`'s own `if not deliverer.called` branch
            # (deliver.py:231): it distinguishes a guard-refused attempt
            # ("vetoed" — she or the pipeline tried and R-2/R-4/R-6/R-18
            # blocked every attempt) from a turn that never tried at all
            # ("silent", R-5's deliberate silence).
            real_outcome = (
                ("recovered" if recovered_delivered else "send_reply")
                if delivered
                else ("vetoed" if handle.deliverer.called else "silent")
            )
            trace = dataclasses.replace(trace, outcome=real_outcome)
            # The shared decision log, written HERE — after
            # `real_outcome` is known and BEFORE the `not delivered` return,
            # so a turn that said nothing is logged as loudly as one that
            # replied. A log that only recorded successes would answer «this
            # room has been quiet» for a room where she has failed four times,
            # which is the opposite of the fact the next decider needs.
            #
            # `real_outcome` is reused rather than re-derived: it is already
            # the finer split (`send_reply` / `recovered` / `vetoed` /
            # `silent`) and a second derivation is a third place the two can
            # disagree. `verdict` is the coarse half the block renders.
            #
            # AWAITED INLINE, not fire-and-forget. It cannot fail this turn —
            # the recorder never raises — and the reply has ALREADY gone out by
            # here, so what it can delay is the trace save and the indicator
            # release, by at most the recorder's own 2s. Said plainly because
            # the wrong word here is an invitation to "optimise" it into a
            # spawn, and a spawned write would not be ordered before the next
            # turn's read, which is the whole property (see
            # `DecisionLogStore.append`: rows are written at turn COMPLETION).
            if self._decisions is not None:
                await self._decisions.record(
                    writer="tag", platform=msg.platform,
                    conversation_id=msg.conversation_id,
                    # THREE outcomes and not two. `failed` for a turn that
                    # chose not to speak would be a mislabel of R-5 — her
                    # deliberate silence is the healthy outcome, not a defect —
                    # and a later decider reading `tag failed` about it would
                    # read a working turn as a broken one. `real_outcome`
                    # already carries the split: `silent` is «she never called
                    # the tool», `vetoed` is «a guard stopped every attempt».
                    verdict=(
                        "replied" if delivered
                        else "silent" if real_outcome == "silent"
                        else "failed"
                    ),
                    reason=real_outcome,
                )
            if not delivered:
                return                       # `finally` still resolves the indicator

            # The tag path's semantic write — NORA'S replies, under the Nora
            # aiPeer, into this conversation's session (Honcho). The human's
            # words are already fed by the ambient memory_feed; this feeds only
            # her side, building her self-model card. `sent_texts` is every
            # message actually delivered (multi-message safe). Spawned off the
            # reply path like every Honcho write.
            # Gated on a MAPPED tagger, like the old feed: her self-model is
            # built only from conversations with a known person (feeding her
            # replies to unmapped senders is deliberately not done).
            # `ctx.tagger is None` means an unmapped sender.
            sent = handle.deliverer.sent_texts
            if sent and ctx.tagger is not None:
                self._spawn(feedback.run(
                    memory=self._memory, ai_peer=self._ai_peer, sent_texts=sent,
                    conversation_id=msg.conversation_id, metrics=self._metrics,
                ))
            completed = True
        except BaseException:
            log.exception("tag pipeline failed (tag=%s); staying silent", request.tag_id)
            # `audience="tag"` as a literal: this pipeline IS the tag path, and
            # the heartbeat's fire turn never enters it. It matters most here of
            # the nine sites — this is R-5's last-resort signal, and A8 filters
            # on `audience="tag"`, so an unlabelled increment would be excluded
            # from the one alarm that is meant to catch a total handler fault.
            self._metrics.inc(
                "nora_silent_failures_total", reason="handler_error",
                platform=msg.platform, audience="tag",
            )
            # The shared decision log, and THIS branch is the failure the log exists
            # to make visible: a tag turn that re-derived an answer participation
            # had just sent and died doing it. Without a row here the log says
            # nothing happened in that room.
            #
            # Written even though `trace` is deliberately dropped a few lines
            # down, and the two are not in tension. The trace is dropped
            # because this branch cannot know WHAT reached the group, so any
            # outcome it wrote would be a guess. The log's claim is narrower
            # and certain: this turn faulted. `handler_error` is the reason
            # the counter above already uses.
            if self._decisions is not None:
                await self._decisions.record(
                    writer="tag", platform=msg.platform,
                    conversation_id=msg.conversation_id,
                    verdict="failed", reason="handler_error",
                )
            # `trace` may already be bound here
            # — a fault inside the never-silent-fallback delivery, the DEC-93
            # recovery delivery, or `check_outcome` itself lands in THIS
            # `except`, having reached `invoke.run`'s return but never the
            # `dataclasses.replace` correction a few lines above. Left alone,
            # the `finally`'s unconditional save would persist that trace with
            # its STILL-PLACEHOLDER `"send_reply"` outcome — a turn that
            # actually crashed and delivered nothing would be retained as if
            # it succeeded normally, a false positive the fold would read as
            # fact. This branch cannot know for certain what (if
            # anything) reached the group before the crash, and a WRONG label
            # is worse than no label at all — the same argument
            # `check_outcome`'s own docstring makes about a mislabelled
            # `nora_silent_failures_total` reason aiming the next
            # investigation at the wrong thing. So this crashed turn joins
            # the "fault before `invoke.run` ever returned" case: no
            # attributable trace, no save (the `finally`'s `trace is not
            # None` guard skips it).
            trace = None
        finally:
            # Resolved here, not after `check_outcome`, so that EVERY exit path
            # a tag has — timeout, llm_error, guard veto, no_send_call, and the
            # outer BaseException — replaces the 👀 with a terminal state. A
            # resolution reachable only from the happy path would strand the
            # 👀 on exactly the failures it exists to make legible.
            indicate.resolve(
                indicator=indicator, delivered=delivered, spawn=self._spawn,
            )
            # THIS TURN'S OWN LATENCY, read HERE and observed further down —
            # before the debrief holds the slot; the split is deliberate.
            # The debrief's cost IS user-visible, but adding it to
            # `nora_tag_latency_seconds` would change what that series
            # measures: an SLO gate that moves because a new stage was added
            # behind it is a broken gate wearing a passing number. The debrief
            # has its own series, `nora_debrief_seconds`, and the two answer
            # two different questions.
            total = time.perf_counter() - started
            # THE DEBRIEF. Four things about this
            # placement are load-bearing and none of them is obvious:
            # 1. **It is in the `finally`, not at the end of the `try`.** It
            #    runs on EVERY completed turn, and three of them — `not ok`
            #    (timeout/llm_error), `not delivered` (an R-5 silence or a
            #    guard veto), and the outer `except` — leave the `try`
            #    before `completed = True` is ever reached. A debrief hung off
            #    the happy path would review only the turns that already work,
            #    and R-5 silence is the decision most likely to be wrong in a
            #    way nobody complains about.
            #
            # 2. **It is BEFORE `release_and_dispatch`, so it holds the
            #    slot** — deliberately. That is what makes it a stage rather
            #    than a background job, and it is why its cost lands on the
            #    SECOND person queued behind a busy conversation rather than
            #    on the person it is reviewing, who already has their reply.
            #    The worst-case hold is asserted at STARTUP by
            #    `Config._the_standing_slot_hold_fits_its_budget`, and this
            #    comment deliberately does NOT restate the arithmetic. The
            #    validator and `STANDING_STORE_CALLS_PER_FOLD` are the single
            #    place that number lives.
            #
            # 3. **`completed` is already final by the time this runs**, which
            #    is the same requirement as «mark the turn completed BEFORE
            #    awaiting the debrief» expressed by placement instead of
            #    by ordering two statements. An escape here must not be able to
            #    leave `completed` False, because `deliver.truncation_reason`
            #    reads it and would report a partial reply — and a false
            #    `nora_partial_replies_total` — on a turn that sent a complete
            #    one.
            #
            # 4. **`debrief.run` never raises**, by construction: its whole
            #    body is inside one `try` whose `except` is `BaseException`
            #    (a cancellation is not an `Exception`). The `try/except/
            #    finally` around the call here is therefore belt-and-braces,
            #    and it stays anyway because what it protects is
            #    unrecoverable. An exception raised inside a `finally` skips
            #    the REST of that `finally` — and the statement immediately
            #    below is the one that releases R-10's per-conversation lock
            #    and dispatches R-30's queue, so a wedged conversation would
            #    answer nobody, forever, with no error anywhere. The `except`
            #    is the second half: this block is inside the `finally`, so a
            #    fault here is past the outer handler and would propagate
            #    straight out of `run` — an outage far wider than one tag,
            #    caused by a stage reviewing a turn that already finished.
            try:
                await self._debrief(
                    trace=trace, platform=msg.platform, culture=culture,
                    profile_room=next(
                        (v for v in profiles if v.entity == "room"), None))
            except BaseException:
                # ABSORBED, not re-raised, and `BaseException` rather than
                # `Exception` for `debrief.run`'s own reason one level down:
                # this is inside the `finally`, so a fault escaping here does
                # not reach the outer `except BaseException` above (that block
                # has already run) — it propagates straight out of `run`,
                # crashing whatever awaited or spawned this tag. That is an
                # outage far wider than one tag, and it would be caused by a
                # stage reviewing a turn that already finished.
                log.exception(
                    "debrief stage faulted (tag=%s); the turn is unaffected",
                    request.tag_id,
                )
            finally:
                # R-10 released, and in the SAME synchronous step R-30 hands
                # the conversation's next waiting tag into the slot this turn
                # just vacated — re-acquiring the lock for it, with no await in
                # the gap, so no incoming tag can race into RUN_NOW between the
                # two. The dequeued turn's context is assembled fresh, so it
                # sees this turn's reply. On an empty queue the dispatch half
                # is a no-op and a drained conversation retains nothing. ONE
                # call, not two statements: a release that skipped the dispatch
                # would strand everyone queued behind this turn until the next
                # tag arrived. This runs on EVERY exit path, so a turn that
                # timed out or faulted still lets the people queued behind it
                # be answered.
                # Retain the raw `TagTrace` BEFORE the release hands the slot
                # to the next waiting tag: that turn's context build reads the
                # shared state this turn's record belongs to, so persistence
                # must complete (or boundedly fail) first. The write is
                # bounded so a slow or down store can still not strand the
                # queue — a failed save is logged and the release below runs
                # regardless, exactly as the spawned write did before.
                if trace is not None and self._tag_trace_store is not None:
                    source = msg.platform + ":" + msg.conversation_id
                    trace = dataclasses.replace(
                        trace, platform=msg.platform, unit=convo.unit if convo else None,
                        bound_rooms=(source,),
                        sent_message_ids=handle.deliverer.sent_message_ids if handle else (),
                    )
                    self._metrics.inc(
                        "nora_tagtrace_writes_total", outcome=trace.outcome)
                    try:
                        await asyncio.wait_for(
                            self._tag_trace_store.save(trace), timeout=10,
                        )
                    except Exception:
                        log.exception(
                            "tag trace save failed (tag=%s)", request.tag_id,
                        )
                self._slots.release_and_dispatch(key)
            # `total` was read BEFORE the debrief, above — see the comment
            # there. Observed here so the two histograms below stay adjacent
            # and keep reading off one number.
            self._metrics.observe("nora_tag_latency_seconds", total)
            self._metrics.observe("nora_overhead_seconds", max(0.0, total - llm_elapsed))
            # One observation per ADMITTED tag, silent turns included as 0 —
            # see `nora.obs.metric_specs`. The `send_reply` budget caps it, so
            # while that budget is 1 the series is flat by construction.
            messages = handle.deliverer.delivered_count if handle is not None else 0
            self._metrics.observe("nora_messages_per_tag", float(messages))
            # THE TURN'S DISPOSITION, on the turn's own span. Every
            # one of these already exists as a metric, and a metric cannot
            # answer «what happened on THIS tag» — the series say what share of turns
            # were silent, the span says this one was, beside the invoke and
            # debrief children that show why. In the `finally` for the same
            # reason every counter here is: it is the one point all five exit
            # paths pass through.
            #
            # `"unattributed"` is not a sixth outcome — it is `trace is None`,
            # which means either `invoke.run` never returned or the outer
            # handler deliberately cleared a crashed turn's placeholder rather
            # than retain a guessed label. Naming it keeps that state readable
            # instead of rendering as an absent attribute, which is
            # indistinguishable from the annotation never having run.
            #
            # THE STANDING COUNTER: incremented HERE, unconditionally, on
            # every exit path this `finally` reaches — unlike
            # `nora_tagtrace_writes_total` a few lines up, this one does not
            # ride on `self._tag_trace_store` being wired, because the
            # question "what did the turn end as" must not go dark just
            # because retention did. `send_reply` vs `recovered` is exactly
            # DEC-93's own distinction: a rising `recovered` share means the
            # route is not honouring `tool_choice=required`, not that the
            # safety net is "working".
            outcome_label = _outcome_label(trace)
            # `audience` is in the label set because a participation
            # outcome landing here unlabelled would be indistinguishable from
            # a tag's. This pipeline passes the literal — it IS the tag path.
            self._metrics.inc(
                "nora_engagement_outcomes_total", outcome=outcome_label, audience="tag",
            )
            annotate(**{
                "nora.outcome": outcome_label,
                "nora.delivered": delivered,
                "nora.stop_reason": stop_reason or "",
                "nora.messages": messages,
                "nora.latency_seconds": total,
                "nora.llm_seconds": llm_elapsed,
            })
            # DEC-40's observable. A turn that sent SOME of its messages and
            # then stopped is not a silent turn — `delivered` is True, the
            # indicator resolves ✅ and Honcho is fed — so nothing R-23 built
            # can see it. Counted here, in the `finally`, because that is the
            # one point every exit path passes through: the cap-cut loop, the
            # timeout, the llm_error, and the outer `BaseException` alike.
            if messages:
                reason = deliver.truncation_reason(
                    stop_reason=stop_reason, completed=completed,
                )
                if reason is not None:
                    log.warning(
                        "partial_reply: %d message(s) sent, then %s (tag=%s)",
                        messages, reason, request.tag_id,
                    )
                    self._metrics.inc(
                        "nora_partial_replies_total", reason=reason, platform=msg.platform,
                    )
            clear_tag_id()
