"""Drive the composed subject's real `App` over generated bridge input.

A complete live-clock journey: real bridge ingress and admission, the
deployment's own scheduling timers waking Cognition, fire and delivery,
then a later correction through the same ingress — never a forced wake
and never a constructed session. The driver owns the `App`'s lifecycle
and the SQL-visible completion signals only; the generated world the
model and services come from belongs to the case-owned environment.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import replace
from typing import Any

import sqlalchemy as sa

from nora.domain.models import NormalizedMessage
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.schema import (
    BRIDGE_ACTION,
    BRIDGE_EVENT,
    BRIDGE_WORK,
    COGNITION_WAKE_OUTCOME,
    REMINDERS,
)
from nora.platform.bridge.envelope import Envelope, canonical_source_key
from nora.platform.bridge.queue import StreamProducer
from nora.platform.bridge.rehydrate import message_to_dict
from nora.subject.effects import prepare_effect_reads
from nora.subject.eval_evidence import EvidenceError

log = logging.getLogger(__name__)


def envelope(
    reader: Any, room: str, timestamp: int, *,
    body: str = "Generated bridge admission input.", tagged: bool = False,
) -> Envelope:
    """One inbound bridge envelope, shaped the way the platform emits it."""
    source = canonical_source_key(
        "signal", reader._account_key, room, "message", str(timestamp))
    message = NormalizedMessage(
        "signal", room, "group", "+852111", "Miles", timestamp, body,
        {"dataMessage": {"message": body}}, message_id=str(timestamp),
        mentions=frozenset({"generated-self"}) if tagged else frozenset())
    return Envelope(
        platform="signal", account_key=reader._account_key, kind="message",
        source_key=source, event_id="generated-admission", conversation_id=room,
        conversation_type="group", sender_id="+852111", sender_name="Miles",
        sent_ms=timestamp, received_ms=timestamp, queued_ms=timestamp,
        time_quality="upstream", message=message_to_dict(message), admission_rev=1)


async def settled_event(engine: Any, canonical: Any, task: asyncio.Task) -> tuple[Any, Any]:
    """The bridge event plus its work rows once admission settles — every
    `failed_retained`/`uncertain` state is an error, never a wait-through."""
    async with asyncio.timeout(15):
        while True:
            async with engine.connect() as conn:
                event = (await conn.execute(sa.select(BRIDGE_EVENT).where(
                    BRIDGE_EVENT.c.id == canonical))).mappings().one_or_none()
                work = (await conn.execute(sa.select(BRIDGE_WORK).where(
                    BRIDGE_WORK.c.event_id == canonical))).mappings().all()
            if (event and event["state"] in {"done", "intentional_skip"}
                    and work and all(w["state"] in {"done", "intentional_skip"}
                                     for w in work)):
                break
            if task.done():
                raise EvidenceError("the app stopped before admission settled")
            if any(w["state"] in {"failed_retained", "uncertain"} for w in work):
                raise EvidenceError(f"bridge work failed to settle: {work!r}")
            await asyncio.sleep(0.02)
    if not all(w["state"] in {"done", "intentional_skip"} for w in work):
        raise EvidenceError(f"bridge work failed to settle: {work!r}")
    return event, work


class JourneyDriver:
    """Owns `App.run` and the journey's SQL-visible completion signals."""

    def __init__(self, subject: Any) -> None:
        from nora.__main__ import App

        self.subject = subject
        runtime = getattr(subject.deps, "bridge_runtime", None)
        if runtime is None:
            raise EvidenceError("the subject composed no bridge runtime")
        self.runtime = runtime
        self.app = App(cfg=subject.cfg, deps=subject.deps)
        self.engine = create_sql_engine(subject.database_url)
        self.task: asyncio.Task | None = None

    def _require_running(self) -> None:
        if self.task is None or not self.task.done():
            return
        try:
            exc = self.task.exception()
        except asyncio.CancelledError:
            exc = None
        raise EvidenceError(f"the app stopped during the journey: {exc!r}")

    async def rows(self, table: Any) -> list[Any]:
        async with self.engine.connect() as conn:
            result = (await conn.execute(sa.select(table))).mappings().all()
            return list(result)

    async def start(self) -> None:
        self.task = asyncio.create_task(self.app.run())
        await prepare_effect_reads(self.engine, self.subject.environment)
        try:
            async with asyncio.timeout(15):
                while not await self.runtime.redis.exists(self.runtime.readers[0].stream):
                    self._require_running()
                    await asyncio.sleep(0.01)
        except TimeoutError:
            self._require_running()
            raise EvidenceError(
                "the bridge reader never came up") from None

    async def ingress(self, body: str, *, event_id: str) -> Envelope:
        """One generated line through real bridge ingress; returns the envelope."""
        reader = self.runtime.readers[0]
        _, room = self.subject.room
        timestamp = int(time.time() * 1000)
        original = envelope(reader, room, timestamp, body=body)
        incoming = replace(original, event_id=event_id,
            message={**(original.message or {}), "body": body,
                     "raw": {"dataMessage": {"message": body}}})
        producer = StreamProducer(self.runtime.redis, reader.stream)
        _, canonical = await producer.enqueue(incoming)
        if self.task is None:
            raise EvidenceError("the app never started for this journey")
        event, _ = await settled_event(self.engine, canonical, self.task)
        if event["state"] != "done":
            raise EvidenceError(f"bridge admission ended {event['state']!r}")
        return incoming

    async def wait_closed_wakes(self, count: int, *, seconds: float) -> list[Any]:
        """At least `count` recorded wake outcomes, every one `closed` — a
        positive production settlement, never an absence-of-error read."""
        async with asyncio.timeout(seconds):
            while True:
                self._require_running()
                outcomes = await self.rows(COGNITION_WAKE_OUTCOME)
                if any(row["outcome"] != "closed" for row in outcomes):
                    raise EvidenceError(f"a wake settled non-closed: {outcomes!r}")
                if len(outcomes) >= count:
                    return outcomes
                await asyncio.sleep(0.25)

    async def wait_delivery(self, *, seconds: float = 30) -> None:
        """Every queued bridge action confirmed and every reminder `done`."""
        async with asyncio.timeout(seconds):
            while True:
                self._require_running()
                actions = await self.rows(BRIDGE_ACTION)
                reminders = await self.rows(REMINDERS)
                if any(row["state"] == "failed" for row in reminders):
                    raise EvidenceError(f"a reminder failed: {reminders!r}")
                if (actions and reminders
                        and all(row["state"] == "confirmed" for row in actions)
                        and all(row["state"] == "done" for row in reminders)):
                    return
                await asyncio.sleep(0.05)

    async def stop(self) -> None:
        """The one teardown: end the app task, shut the app down, release the
        engine — a task that already failed still gets its shutdown."""
        try:
            if self.task is not None:
                task, self.task = self.task, None
                task.cancel()
                # The app task's own failure is the drive's named error
                # (`_require_running`); awaiting it here retrieves it so it
                # can neither skip the shutdown nor report as unretrieved.
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
                await self.app.shutdown()
        finally:
            await self.engine.dispose()

    async def __aenter__(self) -> JourneyDriver:
        try:
            await self.start()
        except BaseException:
            # A failed or cancelled start still owns the app task it created
            # and the engine; unwind through the same stop a clean exit
            # takes, then let the original failure surface.
            try:
                await self.stop()
            except BaseException:
                # The start failure leads; a cleanup failure is logged, never
                # substituted for it.
                log.exception("journey start cleanup failed")
            raise
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        # The `IsolatedServices` pattern: a body cancelled mid-drive still
        # finishes its own teardown before the cancellation continues.
        # `asyncio.wait` never cancels the cleanup task, so cancelling the
        # caller — again and again — delays only the propagation.
        cleanup = asyncio.create_task(self.stop())
        cancelled: asyncio.CancelledError | None = None
        while not cleanup.done():
            try:
                await asyncio.wait({cleanup})
            except asyncio.CancelledError as caught:
                cancelled = caught
        teardown = (asyncio.CancelledError() if cleanup.cancelled()
                    else cleanup.exception())
        if cancelled is not None:
            if teardown is not None:
                log.error("journey teardown failed during cancellation: %r",
                          teardown)
            raise cancelled
        if teardown is not None:
            # The drive's own failure is the one to surface; a teardown
            # failure beside it is reported, never substituted for it.
            if exc is None:
                raise teardown
            log.exception("journey teardown failed beside the drive failure")
