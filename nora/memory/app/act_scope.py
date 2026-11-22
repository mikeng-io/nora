"""Pass authority and ordinals for executors entered during that pass."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from nora.memory.app.acts import ActEnvelope
from nora.memory.domain.act_executors import EXECUTORS
from nora.memory.domain.acts import Act, ActAuthority, ActIdentity


@dataclass(slots=True)
class ActPass:
    authority: ActAuthority
    trigger_id: str
    envelope: ActEnvelope | None = None
    ordinals: Counter[str] = field(default_factory=Counter)
    requests: dict[tuple[str, str], int] = field(default_factory=dict)
    shown: set[str] = field(default_factory=set)


_active_pass: ContextVar[ActPass | None] = ContextVar("act_pass", default=None)


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
) -> Act:
    spec = EXECUTORS[executor]
    active = _active_pass.get() if from_pass else None
    if active is not None:
        authority = active.authority
        event = active.trigger_id
        request = (executor, request_id) if request_id else None
        if request is not None and request in active.requests:
            ordinal = active.requests[request]
        else:
            ordinal = active.ordinals[executor]
            active.ordinals[executor] += 1
            if request is not None:
                active.requests[request] = ordinal
    return Act(ActIdentity(executor, event, ordinal), spec.kind, destination, authority,
               spec.guarantee, key)
