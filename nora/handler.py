"""Ambient ingest + tagging orchestration (spec C.8).

Ambient is a deterministic write path with exactly one branch: tag detection. It never
constructs an Agent (R-1) and never blocks on Honcho.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid

from .agent import factory, prefill
from .agent.guards import Deliverer, ReplyGuard
from .agent.tools import build_tools
from .models import NormalizedMessage, TagContext
from .obs.metrics import METRICS
from .store import db
from .tag import Debounce, is_tagged

log = logging.getLogger("nora.handler")


class Handler:
    def __init__(self, *, cfg, adapter, persons, honcho, persona_store,
                 metrics=METRICS, invoke_fn=None, create_agent_fn=None,
                 clock=time.monotonic) -> None:
        self.cfg = cfg
        self.adapter = adapter
        self.persons = persons
        self.honcho = honcho
        self.persona_store = persona_store
        self.metrics = metrics
        self._clock = clock
        self._debounce = Debounce(cfg.trigger.min_interval_seconds, clock=clock)
        self._in_flight: set[tuple[str, str]] = set()
        self._bg: set[asyncio.Task] = set()
        self._invoke_fn = invoke_fn
        self._create_agent_fn = create_agent_fn
        self._last_prefill = None
        self._last_limits = None

    def _spawn(self, coro) -> None:
        """Run off the listener's path so ingestion never stalls behind a slow call.

        The set holds a strong reference — asyncio keeps only weak ones, so a bare
        create_task can be garbage-collected mid-flight. (Carried over from v1.)
        """
        task = asyncio.create_task(coro)
        self._bg.add(task)
        task.add_done_callback(self._done)

    def _done(self, task: asyncio.Task) -> None:
        self._bg.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("background task failed: %s", task.exception())

    async def on_event(self, raw_event: dict) -> None:
        msg = await self.adapter.parse(raw_event)
        if msg is None:
            return
        self.metrics.inc("nora_messages_total", platform=msg.platform)

        try:
            await db.append(
                self.cfg.buffer.db_path, msg,
                max_per_conversation=self.cfg.buffer.max_per_conversation,
            )
        except Exception:                      # noqa: BLE001 — never kill the listener
            log.exception("db append failed; dropping message")
            self.metrics.inc("nora_errors_total", stage="ambient")
            return

        ref = self.persons.resolve(msg.platform, msg.sender_id)
        if ref is not None:                    # unmapped senders are never fed (D6 amendment)
            self._spawn(self._feed_ambient(ref.honcho_peer_id, msg))

        if not is_tagged(msg.body, self.cfg.trigger.text):
            return
        if msg.sender_id == self.adapter._account:      # R-12 re-check at the handler
            log.info("ignoring a trigger from our own account")
            return

        key = (msg.platform, msg.conversation_id)
        if not self._debounce.allow(key):
            log.info("debounced tag in %s", msg.conversation_id)
            self.metrics.inc("nora_tags_debounced_total")
            return
        if key in self._in_flight:                       # R-10: drop, do not queue
            log.info("tag_dropped_in_flight in %s", msg.conversation_id)
            self.metrics.inc("nora_tags_dropped_total", reason="in_flight")
            return
        self._in_flight.add(key)
        self.metrics.inc("nora_tags_total")
        self._spawn(self.handle_tag(msg))

    async def _collect_context(self, msg: NormalizedMessage, tag_id: str) -> TagContext:
        transcript: tuple = ()
        try:
            transcript = tuple(await db.recent(
                self.cfg.buffer.db_path, msg.conversation_id, self.cfg.buffer.context_window
            ))
        except Exception:                    # noqa: BLE001 — thinner context beats silence
            log.exception("context collection failed; continuing with an empty transcript")
            self.metrics.inc("nora_errors_total", stage="context")

        ref = self.persons.resolve(msg.platform, msg.sender_id)
        card: tuple = ()
        if ref is not None:
            card = tuple(await self.honcho.peer_card(ref.honcho_peer_id))
        return TagContext(
            tag_id=tag_id, message=msg, tagger=ref, transcript=transcript,
            peer_card=card, started_at=time.perf_counter(),
        )

    async def _invoke(self, persona, tools, pre_fill, guard, tag_message, limits, ctx) -> None:
        self._last_prefill = pre_fill
        self._last_limits = limits
        tools_by_name = {t.tool_name: t for t in tools}
        if self._invoke_fn is not None:
            if self._create_agent_fn is not None:
                self._create_agent_fn()
            await self._invoke_fn(tools_by_name, guard, tag_message)
            return
        client = factory.create_client(self.cfg.llm)
        model = factory.create_model_provider(self.cfg.llm.default, client)
        agent = factory.create_agent(
            model=model, system_prompt=persona.text, tools=tools, pre_fill=pre_fill,
            guard=guard, trace_attributes={"tag_id": ctx.tag_id},
        )
        await factory.invoke(agent, tag_message, invocation_state={}, limits=limits)

    async def _feed_exchange(self, ctx, tagger_name: str, deliverer) -> None:
        text = deliverer.last_text or ""
        await self.honcho.feed(
            ctx.tagger.honcho_peer_id,
            f"[{tagger_name}]: {ctx.message.body}\n[{self.cfg.persona.display_name}]: {text}",
        )

    async def handle_tag(self, msg: NormalizedMessage) -> None:
        key = (msg.platform, msg.conversation_id)
        tag_id = uuid.uuid4().hex
        started = time.perf_counter()
        ctx = None
        try:
            ctx = await self._collect_context(msg, tag_id)
            persona = self.persona_store.get(applies_to="tag")
            log.info(
                "tag start (tag_id=%s conversation_id=%s sender_id=%s person_id=%s "
                "persona_sha256=%s persona_versions=%s)",
                tag_id, msg.conversation_id, msg.sender_id,
                ctx.tagger.person_id if ctx.tagger else None,
                persona.sha256, persona.versions(),
            )

            target = msg.target()
            deliverer = Deliverer(
                db_path=self.cfg.buffer.db_path, adapter=self.adapter, target=target,
                persona=persona, canary=self.cfg.persona.canary, tag_id=tag_id,
                nora_sender_id=self.cfg.signal_cli.nora_sender_id,
                max_per_conversation=self.cfg.buffer.max_per_conversation,
                metrics=self.metrics,
            )
            guard = ReplyGuard(conversation_id=msg.conversation_id, deliverer=deliverer,
                               metrics=self.metrics)
            tools = build_tools(
                db_path=self.cfg.buffer.db_path, conversation_id=msg.conversation_id,
                conversation_type=msg.conversation_type, persons=self.persons,
                honcho=self.honcho, deliverer=deliverer,
                context_window=self.cfg.buffer.context_window,
            )
            tagger_name = self.persons.display_name(msg)
            pre_fill = prefill.build_prefill(
                ctx, display_name=self.cfg.persona.display_name,
                context_window=self.cfg.buffer.context_window,
            )
            tag_message = prefill.build_tag_message(ctx, tagger_name=tagger_name)
            limits = factory.build_limits(self.cfg.llm.default, self.cfg.llm.limits)

            try:
                await asyncio.wait_for(
                    self._invoke(persona, tools, pre_fill, guard, tag_message, limits, ctx),
                    timeout=self.cfg.trigger.invocation_timeout_seconds,
                )
            except TimeoutError:
                log.error("invocation_timeout (tag=%s)", tag_id)
                self.metrics.inc("nora_silent_failures_total", reason="invocation_timeout")
                return
            except Exception:                # noqa: BLE001 — R-5: never speak on failure
                log.exception("llm_error (tag=%s); staying silent", tag_id)
                self.metrics.inc("nora_silent_failures_total", reason="llm_error")
                return

            if not deliverer.delivered:
                if not deliverer.attempted:
                    log.warning("no_send_call (tag=%s); dropping the model's text", tag_id)
                    self.metrics.inc("nora_silent_failures_total", reason="no_send_call")
                return

            if ctx.tagger is not None:       # step 4: exactly one exchange feed
                self._spawn(self._feed_exchange(ctx, tagger_name, deliverer))
        except BaseException:                # noqa: BLE001 — R-5, absolute
            log.exception("handle_tag failed (tag=%s); staying silent", tag_id)
            self.metrics.inc("nora_silent_failures_total", reason="handler_error")
        finally:
            self._in_flight.discard(key)     # R-10: released on EVERY exit path
            self.metrics.observe("nora_tag_latency_seconds",
                                 time.perf_counter() - started)

    async def _feed_ambient(self, peer_id: str, msg: NormalizedMessage) -> None:
        await self.honcho.ensure_peer(peer_id)
        ok = await self.honcho.feed(peer_id, msg.body)
        self.metrics.inc("honcho_feed_success" if ok else "honcho_feed_failure")
