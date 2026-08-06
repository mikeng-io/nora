"""`EngagementPipeline` — runs admission → context → doctrine → tools →
invoke → deliver → feedback for a tag `nora.ingest` already decided to
engage (spec §4.4). A readable table of contents, not a decision-maker:
every branch belongs to the stage that owns it (`nora.engagement.stages.*`)
or, for doctrine/tools, to the collaborator it delegates straight to
(`DoctrineStore.get`, `nora.agent.registry.ToolRegistry.build`) — neither is
complex enough to earn its own stage module.

**R-10 lock ownership is split across two modules on purpose** — documented
on both halves so a reader landing on either one sees the whole contract.
`nora.engagement.stages.admission.run` ACQUIRES: it adds `key` to
`in_flight` on admission, and only on admission. THIS module's `run` RELEASES
via its `finally` — but only for a tag admission actually admitted. `run`
below calls `admission.run` OUTSIDE the `try/finally` that owns `key`, and
opens that block only once admission returns True. That ordering matters: if
the lock-release `finally` wrapped the admission call too, a REJECTED tag
(debounced, self-tagged, or dropped because another tag is already in flight
for this conversation) would still discard `key` from `in_flight` on the way
out — for the in-flight-drop case specifically, that key belongs to a
DIFFERENT, still-running tag, and discarding it would release that other
tag's lock out from under it.

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

from nora.agent.prefill import build_prefill, build_tag_message
from nora.agent.registry import ToolRegistry
from nora.agent.room import Room
from nora.domain.ports import AuditStore, EngagementRequest, MemoryStore, RosterSource
from nora.obs.logging import bind_tag_id, clear_tag_id
from nora.persons import Persons
from nora.tag import Debounce

from .stages import admission, deliver, feedback, indicate, invoke
from .stages.invoke import AgentRunner

log = logging.getLogger("nora.engagement.pipeline")


class EngagementPipeline:
    def __init__(
        self,
        *,
        adapter: Any,
        audit: AuditStore,
        memory: MemoryStore,
        persons: Persons,
        doctrine_store: Any,
        context_assembler: Any,
        runner: AgentRunner,
        tool_registry: ToolRegistry,
        debounce: Debounce,
        canary: str,
        display_name: str,
        nora_sender_id: str,
        context_window: int,
        timeout_seconds: float,
        limits: dict,
        metrics: Any,
        spawn: Callable[[Coroutine[Any, Any, None]], Any],
        llm_state: Any = None,
        reactions: bool = True,
        roster_source: RosterSource | None = None,
    ) -> None:
        self._adapter = adapter
        self._audit = audit
        self._memory = memory
        self._persons = persons
        self._doctrine_store = doctrine_store
        self._context = context_assembler
        self._runner = runner
        self._tool_registry = tool_registry
        self._debounce = debounce
        self._canary = canary
        self._display_name = display_name
        self._nora_sender_id = nora_sender_id
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
        self._in_flight: set[tuple[str, str]] = set()

    async def run(self, request: EngagementRequest) -> None:
        """Admit, then (if admitted) run the tag. See module docstring for
        why admission is called here, outside the lock-release `finally`.

        `resolve_identity()` is memoized on the adapter after its first call
        (and is already forced to succeed once, fail-fast, at startup by
        `nora.platform.registry.AdapterRegistry.from_config`), so calling it
        again here is an in-process lookup, not a fresh network round-trip —
        R-12 (`nora.engagement.stages.admission`) needs the resolved identity,
        not the adapter itself, to check self-tag membership.
        """
        message = request.message
        identity = await self._adapter.resolve_identity()
        admitted = await admission.run(
            message=message, identity=identity, debounce=self._debounce,
            in_flight=self._in_flight, metrics=self._metrics,
        )
        if not admitted:
            return
        # `identity` is threaded on rather than re-resolved: the room-scoped
        # tools need Nora's own id forms to leave herself out of the group's
        # member list, and `PlatformIdentity.aliases` is the same flattened set
        # R-12 tests membership in (design/10 §5) — the one set that survives
        # a member arriving as a phone number here and an ACI there.
        await self._run_admitted(request, identity)

    async def _run_admitted(self, request: EngagementRequest, identity: Any) -> None:
        msg = request.message
        key = (msg.platform, msg.conversation_id)
        started = time.perf_counter()
        llm_elapsed = 0.0
        delivered = False
        # Bound BEFORE the `try` so the `finally` can read the tag's delivery
        # count on every exit path, including one that faults inside
        # `context.assemble` before there is a handle at all. Reading it off
        # the `Deliverer` rather than tracking a second local is deliberate:
        # a local would be one more thing to remember to increment when step 5
        # makes the count able to exceed 1.
        handle: deliver.DeliveryHandle | None = None
        # Every log line emitted for the rest of this tag — across every stage
        # this method calls into — carries tag_id without any of them naming
        # it explicitly (nora.obs.logging's contextvar bridge).
        bind_tag_id(request.tag_id)
        # 👀 on the tagging message, the moment the tag is admitted. Scheduled
        # through `spawn`, so the round-trip to the platform is never in front
        # of the model call — see `nora.engagement.stages.indicate`'s docstring
        # for that, for why a reaction cannot touch R-2/R-3, and for why a
        # failed turn resolves to ⚠️ rather than a ✅ that would claim a reply
        # nobody is going to receive. Constructed OUTSIDE the `try` so the
        # `finally` below can never reference it unbound; `begin` only spawns,
        # and cannot raise past its own guard.
        # `engagement.reactions.enabled = false` leaves `indicator` None, which
        # `indicate.resolve` already treats as "nothing to replace" — so the
        # flag is one branch here and none in the `finally`. A second branch
        # down there could drift out of step with this one and strand a 👀.
        indicator = (
            indicate.begin(
                adapter=self._adapter, message=msg, tag_id=request.tag_id,
                metrics=self._metrics, spawn=self._spawn,
            )
            if self._reactions
            else None
        )
        try:
            ctx = await self._context.assemble(request=request)
            doctrine = self._doctrine_store.get(applies_to="tag")
            log.info(
                "tag start (tag_id=%s conversation_id=%s sender_id=%s person_id=%s "
                "doctrine_sha256=%s doctrine_versions=%s)",
                request.tag_id, msg.conversation_id, msg.sender_id,
                ctx.tagger.person_id if ctx.tagger else None,
                doctrine.sha256, doctrine.versions(),
            )

            handle = deliver.build(
                store=self._audit, adapter=self._adapter, target=msg.target(),
                doctrine=doctrine, canary=self._canary, tag_id=request.tag_id,
                nora_sender_id=self._nora_sender_id, conversation_id=msg.conversation_id,
                budgets=self._tool_registry.budgets(),
                terminal=self._tool_registry.terminal_tools(), metrics=self._metrics,
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
                return

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

            if ctx.tagger is not None:   # step 4: exactly one exchange feed
                self._spawn(feedback.run(
                    memory=self._memory, ctx=ctx, tagger_name=tagger_name,
                    display_name=self._display_name, deliverer=handle.deliverer,
                    metrics=self._metrics,
                ))
        except BaseException:
            log.exception("tag pipeline failed (tag=%s); staying silent", request.tag_id)
            self._metrics.inc(
                "nora_silent_failures_total", reason="handler_error", platform=msg.platform,
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
            self._in_flight.discard(key)     # R-10: released on EVERY exit path
            total = time.perf_counter() - started
            self._metrics.observe("nora_tag_latency_seconds", total)
            self._metrics.observe("nora_overhead_seconds", max(0.0, total - llm_elapsed))
            # One observation per ADMITTED tag, silent turns included as 0 —
            # see `nora.obs.metric_specs`. R-2 caps it at 1 today, so the
            # series is flat by construction; step 5 is what makes it move,
            # and a baseline recorded afterwards would not be one.
            self._metrics.observe(
                "nora_messages_per_tag",
                float(handle.deliverer.delivered_count) if handle is not None else 0.0,
            )
            clear_tag_id()
