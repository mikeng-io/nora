"""The tool slots every executor draws on, and the executor that bounds an engagement pass's calls.

An interactive executor handed a wait window hands a call over when it is still running at the
window (`BoundedToolExecutor`); background executors never do.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
from contextvars import copy_context
from typing import Any

from strands.tools.executors import ConcurrentToolExecutor
from strands.types._events import ToolResultEvent
from strands.types.tools import ToolResult

from ..domain.computer_tasks import ComputerAdmissionWindow, computer_admission_window
from ..memory import Outcome, hand_over_tool, record_pass_tool
from .guards import _unwrap_tool_status

log = logging.getLogger("nora.tool_executor")

#: How many model-controlled tool calls may run at once across EVERY agent in the process. A
#: parallel batch is not limited by Strands, and each turn, dream and sub-agent loop used to hold
#: its own unbounded batch; overlapping turns multiplied that.
TOOL_CONCURRENCY = 8

#: Slots background work (the Dreamer, the sub-agent worker) may never take. A user-facing turn
#: finds one free even while eight long-running background tools run; without the reserve they
#: could hold every slot and the turn's tool would wait out its timeout.
INTERACTIVE_RESERVE = 2


class ToolSlots:
    """The in-flight tool calls that every executor sharing one instance draws on: `limit` in all.

    Background work may hold at most `limit - reserve` of them at once, so the `reserve` left are
    for interactive turns whatever the background is doing. Both gates are `asyncio.Semaphore`s,
    which bind to the event loop they first wait on: one instance serves one loop, the process's.
    """

    def __init__(self, limit: int, *, reserve: int = 0) -> None:
        if limit < 1:
            raise ValueError("a tool executor needs at least one slot")
        if not 0 <= reserve < limit:
            raise ValueError("background work must leave at least one slot for interactive turns")
        self.limit = limit
        self._total = asyncio.Semaphore(limit)
        self._background = asyncio.Semaphore(limit - reserve)
        self.in_flight = 0
        self.waiting = 0
        self.peak = 0

    async def acquire(self, *, background: bool) -> None:
        self.waiting += 1
        try:
            if background:
                await self._background.acquire()
                try:
                    await self._total.acquire()
                except BaseException:
                    self._background.release()
                    raise
            else:
                await self._total.acquire()
        finally:
            self.waiting -= 1
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)

    def release(self, *, background: bool) -> None:
        self.in_flight -= 1
        self._total.release()
        if background:
            self._background.release()


#: Tools that mean something only inside their pass: replies and other deliveries, the graph's
#: proposals and commits. They are never handed over, however long they run.
NEVER_HANDED_OVER = frozenset({
    "send_reply", "reaction_send", "send_gif", "send_to", "media_send",
    "memory_propose", "graph_propose", "intention_commit",
})

#: Tool statuses that end a handed-over call as failed rather than done.
_FAILED_STATUSES = frozenset({"unavailable", "refused"})


class BoundedToolExecutor(ConcurrentToolExecutor):
    """Concurrent tool execution drawing on one `ToolSlots`, shared by every agent given it.

    Each tool call of a batch is a task (Strands' concurrent executor), and each task takes a slot
    before it starts and gives it back when its stream ends, however it ends. Results keep the
    model's order because Strands collects them by task, not by completion. A cancelled turn gives
    its slots back the same way, so it cannot starve the next turn. A `background` executor takes
    its slots under the reserve (see `ToolSlots`); an interactive one takes them from the whole.

    An interactive executor given `wait_seconds` bounds each call of an engagement pass: a call
    still running then is handed over (`_hand_over`) and the step gets `started` in its place.
    Without it, or for a call that must not be handed over, a tool that never returns holds its
    slot until it is cancelled; the tools' own timeouts (MCP read timeouts, the browse task's
    clock) are what end one. A tool must not await another agent that shares this executor, or
    the two can wait on each other's slots.
    """

    def __init__(
        self, slots: ToolSlots, *, background: bool = False, wait_seconds: float | None = None,
    ) -> None:
        super().__init__()
        if wait_seconds is not None and not 0 < wait_seconds < float("inf"):
            raise ValueError("a tool wait window must be positive and finite")
        self.slots = slots
        self.background = background
        self.wait_seconds = None if background else wait_seconds
        self._handed: set[asyncio.Task[None]] = set()

    @property
    def in_flight(self) -> int:
        return self.slots.in_flight

    @property
    def waiting(self) -> int:
        return self.slots.waiting

    @property
    def peak(self) -> int:
        return self.slots.peak

    @property
    def handed_over(self) -> tuple[asyncio.Task[None], ...]:
        """The handed-over calls still running, each as the task that records its outcome."""
        return tuple(self._handed)

    async def _task(
        self, agent: Any, tool_use: Any, tool_results: list, cycle_trace: Any, cycle_span: Any,
        invocation_state: Any, task_id: int, task_queue: asyncio.Queue, task_event: asyncio.Event,
        stop_event: object, structured_output_context: Any, invocation_context: Any = None,
    ) -> None:
        await self.slots.acquire(background=self.background)
        held = True
        try:
            rest = (cycle_trace, cycle_span, invocation_state)
            tail = (structured_output_context, invocation_context)
            if self.wait_seconds is None or tool_use["name"] in NEVER_HANDED_OVER:
                await super()._task(
                    agent, tool_use, tool_results, *rest, task_id, task_queue, task_event,
                    stop_event, *tail,
                )
                return
            # ponytail: the window starts once the slot is held; waiting for a slot is
            # unbounded, as it always was, and only a saturated pool makes it long.
            events: asyncio.Queue = asyncio.Queue()
            resume, done = asyncio.Event(), object()
            results: list[ToolResult] = []
            window = ComputerAdmissionWindow()
            context = copy_context()
            context.run(computer_admission_window.set, window)
            run = asyncio.create_task(super()._task(
                agent, tool_use, results, *rest, task_id, events, resume, done, *tail,
            ), context=context)
            handed = False
            try:
                handed = await self._relay(
                    run, tool_use, tool_results, results, events, resume, done,
                    task_id, task_queue, task_event, stop_event, window,
                )
            finally:
                if not handed:
                    run.cancel()
                    await asyncio.gather(run, return_exceptions=True)
            # A handed-over call runs on without a slot: the release below gives it up now.
        finally:
            if held:
                self.slots.release(background=self.background)

    async def _relay(
        self, run: asyncio.Task, tool_use: Any, tool_results: list, results: list,
        events: asyncio.Queue, resume: asyncio.Event, done: object, task_id: int,
        task_queue: asyncio.Queue, task_event: asyncio.Event, stop_event: object,
        window: ComputerAdmissionWindow,
    ) -> bool:
        """Pass the call's events on; True once handed over (the step has `started`), else False."""
        loop = asyncio.get_running_loop()
        deadline: float | None = loop.time() + (self.wait_seconds or 0.0)
        while True:
            try:
                timeout = None if deadline is None else max(0.0, deadline - loop.time())
                _, event = await asyncio.wait_for(events.get(), timeout)
            except TimeoutError:
                async with window.lock:
                    handover = await hand_over_tool(tool_use)
                    if handover is not None and handover.act is not None:
                        window.report_act_id = handover.act_id
                if handover is None:
                    deadline = None
                    continue
                result = self._started(tool_use, handover.act_id)
                await self._record_started(tool_use, result)
                settle = asyncio.create_task(self._settle(run, events, resume, done, results,
                                                          handover))
                self._handed.add(settle)
                settle.add_done_callback(self._handed.discard)
                tool_results.append(result)
                task_queue.put_nowait((task_id, ToolResultEvent(result)))
                await task_event.wait()
                task_event.clear()
                task_queue.put_nowait((task_id, stop_event))
                return True
            if event is done:
                tool_results.extend(results)
                task_queue.put_nowait((task_id, stop_event))
                return False
            task_queue.put_nowait((task_id, event))
            await task_event.wait()
            task_event.clear()
            resume.set()

    def _started(self, tool_use: Any, act_id: str) -> ToolResult:
        note = (
            f"Still running after {self.wait_seconds:g}s, and it keeps going. Its outcome is "
            f"recorded on act {act_id}. To report it later, commit an intention with an "
            "act_event trigger on that act id."
        )
        body = {"status": "started", "act": act_id, "note": note}
        return {
            "toolUseId": str(tool_use["toolUseId"]), "status": "success",
            "content": [{"text": json.dumps(body)}],
        }

    @staticmethod
    async def _record_started(tool_use: Any, result: ToolResult) -> None:
        try:
            await record_pass_tool(dict(tool_use), "started", result["content"])
        except Exception:
            log.exception("handed-over tool call evidence write failed")

    async def _settle(
        self, run: asyncio.Task, events: asyncio.Queue, resume: asyncio.Event, done: object,
        results: list[ToolResult], handover: Any,
    ) -> None:
        """Let a handed-over call finish and record how it ended on its act."""
        failure: Exception | None = None
        try:
            while True:
                _, event = await events.get()
                if event is done:
                    break
                if isinstance(event, Exception):
                    failure = event
                resume.set()
        except asyncio.CancelledError:
            run.cancel()
            await asyncio.gather(run, return_exceptions=True)
            await self._record(handover, Outcome("uncertain"))
            raise
        await self._record(handover, _handover_outcome(results, failure))

    @staticmethod
    async def _record(handover: Any, outcome: Outcome) -> None:
        try:
            await handover.settle(outcome)
        except Exception:
            log.exception("handed-over tool call outcome could not be recorded")


def _handover_outcome(results: list[ToolResult], failure: Exception | None) -> Outcome:
    """How a handed-over call ended: its own result, or `uncertain` when none came back."""
    if failure is not None or not results:
        return Outcome("uncertain")
    result = results[0]
    status = _unwrap_tool_status(result)
    if result["status"] == "error" or status in _FAILED_STATUSES:
        return Outcome("failed", list(result["content"]))
    return Outcome("done", list(result["content"]))


@functools.cache
def shared_tool_slots() -> ToolSlots:
    """The process's tool slots, built on first use: one object, so every executor draws on one
    limit. `functools.cache` makes it the same object for every caller."""
    return ToolSlots(TOOL_CONCURRENCY, reserve=INTERACTIVE_RESERVE)


@functools.cache
def shared_tool_executor() -> BoundedToolExecutor:
    """The interactive executor, the tag loop's default: it may take any slot."""
    return BoundedToolExecutor(shared_tool_slots())


@functools.cache
def shared_background_executor() -> BoundedToolExecutor:
    """The background executor, the Dreamer's and the sub-agent's default: the same slots, but
    at most `TOOL_CONCURRENCY - INTERACTIVE_RESERVE` of them at once."""
    return BoundedToolExecutor(shared_tool_slots(), background=True)
