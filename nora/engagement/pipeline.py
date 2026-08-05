"""`EngagementPipeline` — runs admission → context → persona → tools →
invoke → deliver → feedback for a tag `nora.ingest` already decided to
engage (spec §4.4). A readable table of contents, not a decision-maker:
every branch belongs to the stage that owns it (`nora.engagement.stages.*`)
or, for persona/tools, to the collaborator it delegates straight to
(`PersonaStore.get`, `nora.agent.registry.ToolRegistry.build`) — neither is
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
from nora.domain.ports import AuditStore, EngagementRequest, MemoryStore
from nora.persons import Persons
from nora.tag import Debounce

from .stages import admission, deliver, feedback, invoke
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
        persona_store: Any,
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
    ) -> None:
        self._adapter = adapter
        self._audit = audit
        self._memory = memory
        self._persons = persons
        self._persona_store = persona_store
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
        self._llm_state = llm_state
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
        await self._run_admitted(request)

    async def _run_admitted(self, request: EngagementRequest) -> None:
        msg = request.message
        key = (msg.platform, msg.conversation_id)
        started = time.perf_counter()
        llm_elapsed = 0.0
        try:
            ctx = await self._context.assemble(request=request)
            persona = self._persona_store.get(applies_to="tag")
            log.info(
                "tag start (tag_id=%s conversation_id=%s sender_id=%s person_id=%s "
                "persona_sha256=%s persona_versions=%s)",
                request.tag_id, msg.conversation_id, msg.sender_id,
                ctx.tagger.person_id if ctx.tagger else None,
                persona.sha256, persona.versions(),
            )

            handle = deliver.build(
                store=self._audit, adapter=self._adapter, target=msg.target(),
                persona=persona, canary=self._canary, tag_id=request.tag_id,
                nora_sender_id=self._nora_sender_id, conversation_id=msg.conversation_id,
                budgets=self._tool_registry.budgets(), metrics=self._metrics,
            )
            tools = self._tool_registry.build(
                store=self._audit, conversation_id=msg.conversation_id,
                conversation_type=msg.conversation_type, persons=self._persons,
                honcho=self._memory, deliverer=handle.deliverer,
                context_window=self._context_window,
            )
            tagger_name = self._persons.display_name(msg)
            pre_fill = build_prefill(
                ctx, display_name=self._display_name, context_window=self._context_window,
            )
            tag_message = build_tag_message(ctx, tagger_name=tagger_name)

            ok, llm_elapsed = await invoke.run(
                runner=self._runner, persona=persona, tools=tools, pre_fill=pre_fill,
                guard=handle.guard, tag_message=tag_message, limits=self._limits, ctx=ctx,
                timeout_seconds=self._timeout_seconds, metrics=self._metrics,
            )
            if not ok:
                return

            delivered = deliver.check_outcome(
                deliverer=handle.deliverer, tag_id=request.tag_id, platform=msg.platform,
                llm_state=self._llm_state, metrics=self._metrics,
            )
            if not delivered:
                return

            if ctx.tagger is not None:   # step 4: exactly one exchange feed
                self._spawn(feedback.run(
                    memory=self._memory, ctx=ctx, tagger_name=tagger_name,
                    display_name=self._display_name, deliverer=handle.deliverer,
                ))
        except BaseException:
            log.exception("tag pipeline failed (tag=%s); staying silent", request.tag_id)
            self._metrics.inc(
                "nora_silent_failures_total", reason="handler_error", platform=msg.platform,
            )
        finally:
            self._in_flight.discard(key)     # R-10: released on EVERY exit path
            total = time.perf_counter() - started
            self._metrics.observe("nora_tag_latency_seconds", total)
            self._metrics.observe("nora_overhead_seconds", max(0.0, total - llm_elapsed))
