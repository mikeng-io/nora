"""Pass authority and ordinals for executors entered during that pass."""

from __future__ import annotations

from collections import Counter
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from nora.memory.app.acts import ActEnvelope
from nora.memory.domain.act_executors import EXECUTORS
from nora.memory.domain.acts import Act, ActAuthority, ActIdentity, Outcome

#: Occasions whose passes never declare an act; they also never hand a tool call over.
_NON_ENGAGEMENT = frozenset({"debrief", "wake", "wake_history", "review"})
#: An executor whose own act already stands for the work of the call that declared it.
_OWN_DELEGATION = frozenset({"computer.dispatch"})


@dataclass(slots=True)
class ActPass:
    authority: ActAuthority
    trigger_id: str
    envelope: ActEnvelope | None = None
    ordinals: Counter[str] = field(default_factory=Counter)
    requests: dict[tuple[str, str], int] = field(default_factory=dict)
    shown: set[str] = field(default_factory=set)
    internal: set[str] = field(default_factory=set)
    running_tools: dict[str, dict[str, Any]] = field(default_factory=dict)
    call_acts: dict[str, list[Act]] = field(default_factory=dict)
    handover_actions: dict[str, Callable[[], Awaitable[bool]]] = field(default_factory=dict)


_active_pass: ContextVar[ActPass | None] = ContextVar("act_pass", default=None)
_current_call: ContextVar[str | None] = ContextVar("act_call", default=None)


class ActRefused(RuntimeError):  # noqa: N818
    """A declaration that cannot enter the act envelope."""


def remember_internal(text: str) -> None:
    active = _active_pass.get()
    if active is not None and text:
        active.internal.add(text)


def internal_corpus() -> frozenset[str]:
    active = _active_pass.get()
    return frozenset(active.internal) if active is not None else frozenset()


@contextmanager
def act_event(
    trigger_id: str, authority: ActAuthority, envelope: ActEnvelope | None = None,
    *, shown: frozenset[str] = frozenset(),
) -> Iterator[None]:
    token = _active_pass.set(ActPass(authority, trigger_id, envelope, shown=set(shown)))
    try:
        yield
    finally:
        _active_pass.reset(token)


def act_pass(
    authority: ActAuthority, envelope: ActEnvelope | None = None,
    *, shown: frozenset[str] = frozenset(),
):
    context = authority.pass_context
    if context is None:
        raise ValueError("pass authority requires its provenance")
    return act_event(context.pass_id, authority, envelope, shown=shown)


def pass_shown() -> set[str] | None:
    """Return the graph IDs the active pass has seen."""
    active = _active_pass.get()
    return active.shown if active is not None else None


def seed_pass_shown(ids: frozenset[str]) -> None:
    shown = pass_shown()
    if shown is not None:
        shown.update(ids)


def begin_pass_tool(tool_use: dict[str, Any]) -> None:
    """Note a tool call starting; acts declared later in this task belong to it."""
    active = _active_pass.get()
    if active is not None and active.envelope is not None:
        _current_call.set(tool_use["toolUseId"])
        active.running_tools[tool_use["toolUseId"]] = tool_use


async def record_pass_tool(
    tool_use: dict[str, Any], status: str, result: object,
) -> None:
    active = _active_pass.get()
    if active is None or active.envelope is None:
        return
    active.running_tools.pop(tool_use.get("toolUseId", ""), None)
    ordinal = active.ordinals["tool_call"]
    active.ordinals["tool_call"] += 1
    await active.envelope.record_tool(active.authority, ordinal, tool_use, status, result)


async def record_interrupted_pass_tools() -> None:
    active = _active_pass.get()
    if active is not None:
        for tool_use in tuple(active.running_tools.values()):
            await record_pass_tool(tool_use, "cancelled", None)


async def record_pass_expression(raw: object) -> None:
    active = _active_pass.get()
    if active is not None and active.envelope is not None:
        await active.envelope.record_expression(active.authority, raw)


async def record_pass_silence() -> None:
    active = _active_pass.get()
    if active is not None and active.envelope is not None:
        await active.envelope.record_silence(active.authority)


def active_act_authority() -> ActAuthority | None:
    active = _active_pass.get()
    return active.authority if active is not None else None


def declare_act(
    executor: str, destination: str, *,
    event: str, authority: ActAuthority, ordinal: int = 0, key: str | None = None,
    from_pass: bool = True, request_id: str | None = None,
    on_handover: Callable[[], Awaitable[bool]] | None = None,
) -> Act:
    spec = EXECUTORS[executor]
    active = _active_pass.get() if from_pass else None
    if active is not None:
        authority = active.authority
        context = authority.pass_context
        if context is not None and context.occasion in _NON_ENGAGEMENT:
            raise ActRefused("non_engagement_pass")
        event = active.trigger_id
        request = (executor, request_id) if request_id else None
        if request is not None and request in active.requests:
            ordinal = active.requests[request]
        else:
            ordinal = active.ordinals[executor]
            active.ordinals[executor] += 1
            if request is not None:
                active.requests[request] = ordinal
    act = Act(ActIdentity(executor, event, ordinal), spec.kind, destination, authority,
              spec.guarantee, key)
    call = _current_call.get()
    if active is not None and call is not None:
        active.call_acts.setdefault(call, []).append(act)
        if on_handover is not None:
            active.handover_actions[act.identity.id] = on_handover
    return act


@dataclass(frozen=True, slots=True)
class HandedOver:
    """The act that stands for a tool call that outlived its pass's wait window."""

    act_id: str
    act: Act | None
    envelope: ActEnvelope

    async def settle(self, outcome: Outcome) -> None:
        """Record how the call ended; a call whose own act stands for it records nothing."""
        if self.act is not None:
            await self.envelope.record_act_outcome(self.act, outcome)


async def hand_over_tool(tool_use: dict[str, Any]) -> HandedOver | None:
    """Declare the delegation for a tool call still running at its wait window.

    None when the pass cannot hand a call over (no envelope, a reflection occasion, the act
    already declared or the pass's claim lost): the caller keeps waiting for the call.
    """
    active = _active_pass.get()
    context = active.authority.pass_context if active is not None else None
    if active is None or active.envelope is None or context is None:
        return None
    if context.occasion in _NON_ENGAGEMENT:
        return None
    call = tool_use["toolUseId"]
    for own in active.call_acts.get(call, ()):
        if own.identity.executor in _OWN_DELEGATION:
            action = active.handover_actions.get(own.identity.id)
            if action is not None and not await action():
                return None
            return HandedOver(own.identity.id, None, active.envelope)
    act = declare_act(
        "tool.handover", f"tool-call:{active.trigger_id}:{call}",
        event=active.trigger_id, authority=active.authority, request_id=call,
    )
    if not await active.envelope.reserve(act):
        return None
    return HandedOver(act.identity.id, act, active.envelope)
