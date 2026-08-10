"""`EngagementPipeline` — runs admission → context → doctrine → tools →
invoke → deliver → feedback for a tag `nora.ingest` already decided to
engage (spec §4.4). A readable table of contents, not a decision-maker:
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
way — narrowing to `except Exception` does not, empirically, leave it stuck.
What DOES break: the fault is no longer absorbed — it propagates out of
`run` uncaught (crashing whatever awaits or spawned this tag, an outage far
wider than one tag) — and `nora_silent_failures_total{reason="handler_error"}`
is never incremented, an observability gap on top of the crash. See this
module's test suite for the probe that proves both.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Coroutine
from typing import Any

from nora.agent.intent_router import route_intent
from nora.agent.outbound import OutboundComposer
from nora.agent.prefill import build_prefill, build_tag_message
from nora.agent.registry import ToolRegistry
from nora.agent.room import Room
from nora.domain.ports import (
    EngagementRequest,
    LinkPreviewSource,
    MemoryStore,
    RecordStore,
    RosterSource,
)
from nora.obs.logging import bind_tag_id, clear_tag_id
from nora.persons import Persons
from nora.policy import Policy
from nora.tag import Debounce

from .mentions import MentionRenderer
from .slots import ConversationSlots
from .stages import admission, deliver, feedback, indicate, invoke
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


class EngagementPipeline:
    def __init__(
        self,
        *,
        adapter: Any,
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
        nora_sender_id: str,
        ai_peer: str,
        context_window: int,
        timeout_seconds: float,
        limits: dict,
        metrics: Any,
        spawn: Callable[[Coroutine[Any, Any, None]], Any],
        slots: ConversationSlots,
        llm_state: Any = None,
        reactions: bool = True,
        roster_source: RosterSource | None = None,
        preview_source: LinkPreviewSource | None = None,
    ) -> None:
        self._adapter = adapter
        self._audit = audit
        self._memory = memory
        self._persons = persons
        self._doctrine_store = doctrine_store
        self._context = context_assembler
        self._runner = runner
        self._tool_registry = tool_registry
        # policy.toml's per-conversation MCP allowlist (spec:
        # mcp-tool-provider-design §3). The ingest pipeline already checked
        # this tag's conversation is admitted at all (nora.ingest.stages.scope,
        # DEC-18) using the SAME Policy instance — this is the second read of
        # it, for a narrower question: which MCP servers, not whether to
        # engage at all.
        self._policy = policy
        self._debounce = debounce
        self._canary = canary
        self._display_name = display_name
        self._nora_sender_id = nora_sender_id
        # The Nora aiPeer id (Honcho v2, design/24): her replies feed under it.
        self._ai_peer = ai_peer
        self._context_window = context_window
        self._timeout_seconds = timeout_seconds
        self._limits = limits
        self._metrics = metrics
        self._spawn = spawn
        self._reactions = reactions
        self._llm_state = llm_state
        # Optional, and `None` is a real production-shaped state, not a test
        # affordance: `nora.app.offline` (the integration and eval harnesses)
        # has no daemon to ask. `nora.agent.room.Room` reports an absent source
        # as UNAVAILABLE rather than as an empty room, so a harness — or a
        # wiring mistake — degrades honestly instead of answering "nobody is
        # here" (R-5).
        self._roster_source = roster_source
        # The OpenGraph fetcher behind an outbound link-preview card. `None`
        # whenever `[preview].enabled` is false — the shipped default — and in
        # every offline composition, which is why it defaults rather than being
        # required: a missing preview is a message without a card, the exact
        # state every deployment is in today, and nothing about it is a wrong
        # claim. (`composer` on `deliver.build` is required for the opposite
        # reason; the split is argued there.)
        self._preview_source = preview_source
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
        DEC-49). That is the whole point of moving it off the turn's start: a
        person whose tag is queued behind two others sees they were SEEN while
        they wait, so the silence they used to get on a dropped tag stops being
        ambiguous. `indicate.begin` only spawns, so it adds no latency to
        anything. A REJECTED (self-tag/debounce) or QUEUE_FULL (flood) tag gets
        no 👀: neither is a person waiting for an answer, and a 👀 on a dropped
        flood tag would be a progress marker that never resolves — the
        stuck-forever bug the indicator exists to avoid.
        """
        message = request.message
        identity = await self._adapter.resolve_identity()
        key = (message.platform, message.conversation_id)
        verdict = admission.run(
            message=message, identity=identity, debounce=self._debounce,
            slots=self._slots, metrics=self._metrics,
        )
        if verdict is Admission.REJECTED or verdict is Admission.QUEUE_FULL:
            return
        indicator = self._begin_indicator(request)
        if verdict is Admission.QUEUED:
            # No await between admission's QUEUED verdict and this enqueue, so
            # the depth admission checked cannot change underneath it and FIFO
            # order is exactly arrival order. `identity` is not stored: it is
            # re-resolved (memoized, in-process) when the tag is dequeued.
            #
            # A zero-argument FACTORY, not a coroutine and not a callback: the
            # closure keeps this tag's request and 👀 handle here, in the
            # pipeline, so `ConversationSlots` needs no reference back into it
            # (see that module's docstring). Nothing is created until the slot
            # actually frees, so a queued tag holds no coroutine object either.
            self._slots.enqueue(key, lambda: self._run_queued(request, indicator))
            return
        # RUN_NOW. `identity` is threaded on rather than re-resolved: the
        # room-scoped tools need Nora's own id forms to leave herself out of the
        # group's member list, and `PlatformIdentity.aliases` is the same
        # flattened set R-12 tests membership in (design/10 §5) — the one set
        # that survives a member arriving as a phone number here and an ACI there.
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
            adapter=self._adapter, message=request.message, tag_id=request.tag_id,
            metrics=self._metrics, spawn=self._spawn,
        )

    async def _run_queued(self, request: EngagementRequest, indicator: Indicator | None) -> None:
        """Run a tag that waited in the FIFO queue. Its context is assembled
        fresh inside `_run_admitted`, at THIS point — so it sees the replies of
        every tag ahead of it in line, which is the correctness reason the queue
        serialises a conversation (see `nora.engagement.queue`). Identity is
        re-resolved here (memoized) rather than carried through the queue."""
        identity = await self._adapter.resolve_identity()
        await self._run_admitted(request, identity, indicator)

    async def _run_admitted(
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
        # a local would be one more thing to remember to increment now that
        # DEC-40 has made the count able to exceed 1.
        handle: deliver.DeliveryHandle | None = None
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
            # An @-mention is a placeholder plus a parallel array, so until
            # DEC-34 every name in the transcript — including Nora's own —
            # reached the model as U+FFFC. Rehydrated HERE rather than at
            # ingest (the audit stores what arrived) or in the prefill (which
            # may not import `nora.platform`), against `raw_json`, which every
            # historical row already carries. See `nora.engagement.mentions`.
            ctx = MentionRenderer(
                persons=self._persons, display_name=self._display_name,
                self_ids=frozenset(getattr(identity, "aliases", ())),
                metrics=self._metrics,
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
            # Computed BEFORE `deliver.build()` and reused for its `budgets=`
            # (final-review Finding 1): `ToolRegistry.budgets()` only folds an
            # MCP tool's budget in when its server name is in `allowed_mcp`,
            # so this pipeline's `ReplyGuard` — and the R-7 unknown-tool veto
            # message it renders — must be built from the SAME set `.build()`
            # below uses, or an unauthorized conversation's guard would still
            # disclose a `pulse_*` name it cannot call.
            convo = self._policy.conversation(msg.platform, msg.conversation_id)
            allowed_mcp = convo.tools if convo is not None else frozenset()
            # DEC-74: the per-conversation location-disclosure trust, sourced the
            # same way `allowed_mcp` is. False (the default) keeps `where_is`'s
            # circle-subset gate strict; a conversation with `location = true`
            # relaxes it for that room only.
            location_trusted = convo.location if convo is not None else False
            handle = deliver.build(
                store=self._audit, adapter=self._adapter, target=msg.target(),
                doctrine=doctrine, canary=self._canary, tag_id=request.tag_id,
                nora_sender_id=self._nora_sender_id, conversation_id=msg.conversation_id,
                budgets=self._tool_registry.budgets(allowed_mcp=allowed_mcp),
                terminal=self._tool_registry.terminal_tools(), metrics=self._metrics,
                audience="tag",
                # The outbound decoration stage (@-mentions + a link preview
                # card). ONE per turn, holding the same `roster_source` the
                # room-scoped tools use — so a mention is confirmed against the
                # platform's own membership list for THIS conversation, lazily
                # and at most once, rather than against the whole address book.
                composer=OutboundComposer(
                    platform=msg.platform, conversation_id=msg.conversation_id,
                    persons=self._persons, metrics=self._metrics,
                    roster_source=self._roster_source,
                    preview_source=self._preview_source,
                ),
                # Phase 2 routing: a precision-first, abstaining classifier over the
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
            )
            # One `Room` per tag, constructed here and closed over by the three
            # room-scoped tools. Nothing reads the platform roster unless one
            # of those tools is actually called — the daemon round trip is
            # inside `Room`, lazily, and cached for the tag — so this stays off
            # both the ingest path and the front of the model call.
            room = Room(
                platform=msg.platform, conversation_id=msg.conversation_id,
                roster_source=self._roster_source, persons=self._persons,
                tagger=ctx.tagger, self_ids=frozenset(getattr(identity, "aliases", ())),
            )
            tools = self._tool_registry.build(
                store=self._audit, conversation_id=msg.conversation_id,
                conversation_type=msg.conversation_type, persons=self._persons,
                honcho=self._memory, deliverer=handle.deliverer,
                room=room, question=msg.body,
                context_window=self._context_window, metrics=self._metrics,
                allowed_mcp=allowed_mcp, location_trusted=location_trusted,
                # DEC-89: the SAME carrier the `Deliverer` above holds. The
                # tools mint links into it and the sender puts back the ones the
                # reply earned and dropped; two carriers would be a feature that
                # collects perfectly and appends nothing.
                links=handle.links,
            )
            tagger_name = self._persons.display_name(msg)
            # STILL NO ROSTER IN THE PROMPT, and now there is a tool instead.
            # `identity.toml` is Nora's address book across EVERY room; handing
            # all of it to one room discloses the existence and names of people
            # who are not in it — asked who was in the group she listed five
            # people who have never been members, a real leak rather than a
            # wrong answer. `group_members` answers that question from the
            # PLATFORM's membership list for this conversation, so the scope is
            # the room itself rather than the address book, and it costs a tool
            # call only when somebody actually asks. The tagger's own aliases
            # still reach the model via `build_tag_message`, scoped by
            # construction.
            pre_fill = build_prefill(
                ctx, display_name=self._display_name, context_window=self._context_window,
            )
            tag_message = build_tag_message(
                ctx, tagger_name=tagger_name, display_name=self._display_name,
            )

            ok, llm_elapsed, stop_reason = await invoke.run(
                runner=self._runner, doctrine=doctrine, tools=tools, pre_fill=pre_fill,
                guard=handle.guard, tag_message=tag_message, limits=self._limits, ctx=ctx,
                timeout_seconds=self._timeout_seconds, metrics=self._metrics,
            )
            if not ok:
                # A timeout or an `llm_error`. Messages may ALREADY have gone
                # out — under multi-message that is the common shape, not an
                # edge case — so this is a partial delivery, not a silent turn,
                # and the `finally` counts it as one.
                #
                # DELIBERATELY NO never-silent fallback here (review 2026-08-09
                # #1, considered and declined): unlike a budget cut-off, this path
                # carries DEC-42 partial-reply accounting (`truncation_reason`),
                # and delivering a fallback would forge a "partial reply" the
                # tests here pin apart. A timeout is ALSO already surfaced as an
                # infra failure on `nora_silent_failures_total{invocation_timeout}`
                # — a real alert, not the silent void the budget fallback closes.
                # Closing this path too is a larger change (DEC-42 reconciliation),
                # left as a flagged follow-up rather than bolted on here.
                return

            # NEVER-SILENT FALLBACK (operator 2026-08-09): the loop was cut off by
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
            # is a partial delivery, not a void.
            cut_off_before_reply = (
                stop_reason in deliver.BUDGET_STOP_REASONS
                and not handle.deliverer.delivered
            )
            if cut_off_before_reply and await handle.deliverer.deliver(
                _EXHAUSTED_FALLBACK, source="fallback"
            ):
                self._metrics.inc(
                    "nora_loop_exhausted_fallbacks_total", reason=stop_reason,
                )

            # `stop_reason` is threaded rather than dropped (R-23): without it
            # a turn Strands cut off at one of R-8's caps is indistinguishable
            # here from a model that chose not to speak, and both would count
            # as `no_send_call`.
            delivered = deliver.check_outcome(
                deliverer=handle.deliverer, tag_id=request.tag_id, platform=msg.platform,
                stop_reason=stop_reason, llm_state=self._llm_state, metrics=self._metrics,
            )
            if not delivered:
                return                       # `finally` still resolves the indicator

            # Step 4: the tag path's semantic write — NORA'S replies, under the
            # Nora aiPeer, into this conversation's session (Honcho v2, design/24
            # §3). The human's words are already fed by the ambient memory_feed;
            # this feeds only her side, building her self-model card. `sent_texts`
            # is every message actually delivered (multi-message safe). Spawned
            # off the reply path like every Honcho write.
            # Gated on a MAPPED tagger, like the old feed: her self-model is
            # built only from conversations with a known person (feeding her
            # replies to unmapped senders is a deliberate later refinement,
            # design/24). `ctx.tagger is None` means an unmapped sender.
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
        finally:
            # Resolved here, not after `check_outcome`, so that EVERY exit path
            # a tag has — timeout, llm_error, guard veto, no_send_call, and the
            # outer BaseException — replaces the 👀 with a terminal state. A
            # resolution reachable only from the happy path would strand the
            # 👀 on exactly the failures it exists to make legible.
            indicate.resolve(
                indicator=indicator, delivered=delivered, spawn=self._spawn,
            )
            # R-10 released, and in the SAME synchronous step R-30 hands the
            # conversation's next waiting tag into the slot this turn just
            # vacated — re-acquiring the lock for it, with no await in the gap,
            # so no incoming tag can race into RUN_NOW between the two. The
            # dequeued turn's context is assembled fresh, so it sees this turn's
            # reply. On an empty queue the dispatch half is a no-op and a
            # drained conversation retains nothing. ONE call, not two
            # statements: a release that skipped the dispatch would strand
            # everyone queued behind this turn until the next tag arrived. This
            # runs on EVERY exit path, so a turn that timed out or faulted still
            # lets the people queued behind it be answered.
            self._slots.release_and_dispatch(key)
            total = time.perf_counter() - started
            self._metrics.observe("nora_tag_latency_seconds", total)
            self._metrics.observe("nora_overhead_seconds", max(0.0, total - llm_elapsed))
            # One observation per ADMITTED tag, silent turns included as 0 —
            # see `nora.obs.metric_specs`. The `send_reply` budget caps it, so
            # while that budget is 1 the series is flat by construction; the
            # baseline was recorded before DEC-40 made it able to move.
            messages = handle.deliverer.delivered_count if handle is not None else 0
            self._metrics.observe("nora_messages_per_tag", float(messages))
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
