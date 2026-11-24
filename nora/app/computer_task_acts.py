"""Dispatch computer jobs by their SQL identity."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime
from functools import partial
from typing import Any
from uuid import UUID

from nora.domain.computer_tasks import ComputerTask, ComputerTaskStore, computer_admission_window
from nora.infra.sql.item_registration import RegistrationScope
from nora.memory import Act, ActAuthority, ActEnvelope, ActInvocation, Outcome, declare_act

log = logging.getLogger("nora.computer.queue")

_EXECUTOR = "computer.dispatch"


class ComputerTaskActs:
    def __init__(
        self, redis: Any, envelope: ActEnvelope | None = None,
        scope: RegistrationScope | None = None, *, tasks: ComputerTaskStore | None = None,
    ) -> None:
        self._redis = redis
        self._envelope = envelope
        self._scope = scope
        self._tasks = tasks

    async def _hand_over(self, task_id: UUID) -> bool:
        if self._tasks is None or self._envelope is None:
            return False
        try:
            if await self._envelope.by_key(_EXECUTOR, str(task_id)) is None:
                return False
            return await self._tasks.hand_over_delivery(task_id)
        except Exception:
            log.exception("computer delivery hand-over could not be recorded (job=%s)", task_id)
            return False

    def _declare(self, task: ComputerTask, *, from_pass: bool) -> Act:
        room = task.origin.get("conversation_id")
        platform = task.origin.get("platform")
        unit = self._scope.room_unit(platform, room) if self._scope and room and platform else None
        return declare_act(
            _EXECUTOR, f"computer-job:{task.id}", event=str(task.id), key=str(task.id),
            authority=ActAuthority(unit, task.origin.get("requester_person_id"), room=room),
            from_pass=from_pass,
            on_handover=partial(self._hand_over, task.id) if self._tasks is not None else None,
        )

    async def _publish(self, stream: str, job_id: str) -> ActInvocation[None]:
        await self._redis.xadd(stream, {"task": job_id})
        # The SQL worker owns the verdict. A stream acknowledgement is only a wakeup.
        return ActInvocation(None, Outcome("uncertain"))

    async def admit(
        self, task: ComputerTask, stream: str, *,
        tasks: ComputerTaskStore, clock: Callable[[], datetime],
    ) -> tuple[ComputerTask, ComputerTask | None]:
        window = computer_admission_window.get()
        # Decide the report route before the queue runner can see the row.
        async with window.lock if window is not None else nullcontext():
            if window is not None and window.report_act_id is not None:
                task = replace(task, origin={**task.origin, "deliver": "job"})
            task = await tasks.enqueue(task)
            claimed = await self.start(
                task, stream, lambda: tasks.try_claim_head(task.id, now=clock()),
            )
        return task, claimed

    async def start(
        self, task: ComputerTask, stream: str, claim: Callable[[], Awaitable[ComputerTask | None]],
    ) -> ComputerTask | None:
        async def dispatch(key: str | None) -> ActInvocation[ComputerTask | None]:
            claimed = await claim()
            if claimed is None:
                try:
                    await self._publish(stream, key or str(task.id))
                except Exception:  # noqa: BLE001 - the queue's recovery owns ambiguous publishes
                    log.warning("computer task publish failed; the reconcile pass will")
            return ActInvocation(claimed, Outcome("uncertain"))

        if self._envelope is None:
            return (await dispatch(str(task.id))).value
        invoked = await self._envelope.invoke(self._declare(task, from_pass=True), dispatch)
        return invoked.value if invoked is not None else None

    async def recover(self, task: ComputerTask, stream: str) -> None:
        job_id = str(task.id)
        if self._envelope is None:
            await self._publish(stream, job_id)
            return

        async def publish(key: str | None) -> ActInvocation[None]:
            if key is None:
                raise ValueError("computer dispatch requires its job id")
            return await self._publish(stream, key)

        act = await self._envelope.by_key(_EXECUTOR, job_id)
        if act is None:
            await self._envelope.invoke(self._declare(task, from_pass=False), publish)
        else:
            await self._envelope.settle(act, replay=publish)

    async def observe_job(self, job_id: str, job: ComputerTask | None) -> None:
        if self._envelope is None:
            return
        try:
            act = await self._envelope.by_key(_EXECUTOR, job_id)
            if act is not None:
                status = job.status if job is not None else ""
                if status == "done":
                    outcome = Outcome("done", job.result if job is not None else None)
                elif status in {"failed", "cancelled"}:
                    outcome = Outcome("failed")
                else:
                    outcome = Outcome("uncertain")
                await self._envelope.record_act_outcome(act, outcome)
        except Exception:
            log.exception("computer act outcome could not be recorded (job=%s)", job_id)
