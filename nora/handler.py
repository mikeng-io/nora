"""Ambient ingest + tagging orchestration (spec C.8).

Ambient is a deterministic write path with exactly one branch: tag detection. It never
constructs an Agent (R-1) and never blocks on Honcho.
"""

from __future__ import annotations

import asyncio
import logging
import time

from .models import NormalizedMessage
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

    async def handle_tag(self, msg: NormalizedMessage) -> None:
        """Provisional — Task 32 replaces this with the full context/invoke/silence order."""
        key = (msg.platform, msg.conversation_id)
        try:
            if self._create_agent_fn is not None:
                self._create_agent_fn()
        finally:
            self._in_flight.discard(key)

    async def _feed_ambient(self, peer_id: str, msg: NormalizedMessage) -> None:
        await self.honcho.ensure_peer(peer_id)
        ok = await self.honcho.feed(peer_id, msg.body)
        self.metrics.inc("honcho_feed_success" if ok else "honcho_feed_failure")
