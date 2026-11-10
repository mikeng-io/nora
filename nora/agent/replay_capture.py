"""Private replay lifecycle and transport capture. Inert outside eval capture."""

from __future__ import annotations

import contextvars
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from types import SimpleNamespace
from typing import Any

from opentelemetry import context, trace
from pydantic import ValidationError
from strands.hooks import (
    AfterInvocationEvent,
    AfterModelCallEvent,
    AfterToolCallEvent,
    BeforeInvocationEvent,
    BeforeModelCallEvent,
    BeforeToolCallEvent,
    HookCallback,
    HookProvider,
    HookRegistry,
)
from strands.tools.decorator import DecoratedFunctionTool, FunctionToolMetadata

from nora.obs.tracing import EVAL_EVIDENCE_KEY, eval_capture_enabled

_INVOCATION: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "replay_invocation", default=None
)
_ATTEMPT: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "replay_attempt", default=None
)


_IDENTITIES: contextvars.ContextVar[list[dict[str, Any]] | None] = contextvars.ContextVar(
    "replay_served_identities", default=None
)


@contextmanager
def served_identities() -> Iterator[list[dict[str, Any]]]:
    identities: list[dict[str, Any]] = []
    token = _IDENTITIES.set(identities)
    try:
        yield identities
    finally:
        _IDENTITIES.reset(token)


def record(kind: str, **payload: Any) -> None:
    sink = context.get_value(EVAL_EVIDENCE_KEY)
    if callable(sink):
        if kind == "transport_response":
            span = trace.get_current_span()
            headers = payload.get("headers", {})
            if headers.get("x-omniroute-model"):
                span.set_attribute("nora.model.served", headers["x-omniroute-model"])
            if headers.get("x-omniroute-provider"):
                span.set_attribute("nora.model.provider", headers["x-omniroute-provider"])
        sink(kind, invocation_id=_INVOCATION.get(), attempt_id=_ATTEMPT.get(), **payload)


def _argument_validation_failure(error: BaseException | None) -> bool:
    """Recognize the SDK's pre-callback validation, never a handler failure."""
    if not isinstance(error, ValueError) or not isinstance(error.__cause__, ValidationError):
        return False
    frames = []
    tb = error.__traceback__
    while tb is not None:
        frames.append(tb.tb_frame.f_code)
        tb = tb.tb_next
    # ponytail: pinned SDK boundary; an SDK path change stays unknown until re-proved.
    return frames[-2:] == [DecoratedFunctionTool.stream.__code__,
                           FunctionToolMetadata.validate_input.__code__]


class ReplayHooks(HookProvider):
    def __init__(self) -> None:
        self.invocation = uuid.uuid4().hex
        self.call = 0
        self.attempt = 0

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        for event in (
            BeforeInvocationEvent,
            AfterInvocationEvent,
            BeforeModelCallEvent,
            AfterModelCallEvent,
            BeforeToolCallEvent,
            AfterToolCallEvent,
        ):
            registry.add_callback(event, self.capture)

    def capture(self, event: Any) -> None:
        _INVOCATION.set(self.invocation)
        if isinstance(event, BeforeInvocationEvent):
            _ATTEMPT.set(None)
            record("invocation_started")
        elif isinstance(event, BeforeModelCallEvent):
            self.attempt += 1
            _ATTEMPT.set(f"{self.invocation}:{self.call}:{self.attempt}")
            record("call_started", call_id=f"{self.invocation}:{self.call}", cancelled=event.cancel)
            if event.cancel:
                record("call_cancelled", reason=event.cancel)
                return
            from .reasoning_strip import dropped_reasoning_blocks
            record("reasoning_carry", dropped_blocks=dropped_reasoning_blocks(
                event.agent.model, event.agent.messages))
        elif isinstance(event, AfterModelCallEvent):
            record(
                "call_finished",
                call_id=f"{self.invocation}:{self.call}",
                retry=event.retry,
                error_type=type(event.exception).__name__ if event.exception else None,
            )
            if not event.retry:
                self.call += 1
                self.attempt = 0
        elif isinstance(event, BeforeToolCallEvent):
            record("tool_started", tool=event.tool_use, cancelled=event.cancel_tool)
        elif isinstance(event, AfterToolCallEvent):
            record(
                "tool_finished",
                tool=event.tool_use,
                result=event.result,
                retry=event.retry,
                error_type=type(event.exception).__name__ if event.exception else None,
                **({"argument_validation": "rejected_before_dispatch"}
                   if _argument_validation_failure(event.exception) else {}),
            )
        elif isinstance(event, AfterInvocationEvent):
            record(
                "invocation_finished",
                outcome=str(getattr(event.result, "stop_reason", "unavailable")),
            )


def hooks() -> list[HookProvider | HookCallback[Any]]:
    return [ReplayHooks()] if eval_capture_enabled() else []


class _Completions:
    def __init__(self, completions: Any, role: str | None = None) -> None:
        self.completions = completions
        self.role = role

    async def create(self, **request: Any) -> Any:
        return await self._request("create", request)

    async def parse(self, **request: Any) -> Any:
        return await self._request("parse", request)

    async def _request(self, method: str, request: dict[str, Any]) -> Any:
        started = time.time_ns()
        record("transport_started")
        try:
            raw = await getattr(self.completions.with_raw_response, method)(**request)
            response = raw.parse()
            headers = {
                key: raw.headers.get(key) for key in ("x-omniroute-model", "x-omniroute-provider")
            }
            identities = _IDENTITIES.get()
            if identities is not None:
                identities.append(headers)
            if not request.get("stream"):
                record(
                    "transport_response",
                    response=response,
                    **({"model_role": self.role} if self.role else {}),
                    headers=headers,
                    identity_availability="observed" if all(headers.values()) else "unavailable",
                    latency_ns=time.time_ns() - started,
                )
                data = response.model_dump()
                messages = [c.get("message", {}) for c in data.get("choices", [])]
                availability = (
                    "observed"
                    if any(m.get("reasoning") or m.get("reasoning_content") for m in messages)
                    else "absent"
                )
                record("transport_finished", reasoning_availability=availability)
                return response
            return _Stream(response, headers, started)
        except BaseException as exc:
            record(
                "transport_finished",
                error_type=type(exc).__name__,
                reasoning_availability="capture_unavailable",
            )
            raise


class _Stream:
    def __init__(self, stream: Any, headers: dict[str, Any], started: int) -> None:
        self.stream, self.headers, self.started = stream, headers, started
        self.reasoning = "absent"

    def __aiter__(self) -> _Stream:
        return self

    async def __anext__(self) -> Any:
        try:
            chunk = await self.stream.__anext__()
        except StopAsyncIteration:
            record("transport_finished", reasoning_availability=self.reasoning)
            raise
        except BaseException as exc:
            record(
                "transport_finished",
                error_type=type(exc).__name__,
                reasoning_availability="capture_unavailable",
            )
            raise
        data = chunk.model_dump()
        for choice in data.get("choices", []):
            delta = choice.get("delta") or {}
            if delta.get("reasoning") or delta.get("reasoning_content"):
                self.reasoning = "observed"
            elif delta.get("redacted_reasoning"):
                self.reasoning = "redacted"
        record(
            "transport_response",
            response=chunk,
            headers=self.headers,
            identity_availability="observed" if all(self.headers.values()) else "unavailable",
            latency_ns=time.time_ns() - self.started,
        )
        return chunk


@asynccontextmanager
async def client_capture(client: Any, *, role: str | None = None) -> AsyncIterator[Any]:
    if not eval_capture_enabled() and _IDENTITIES.get() is None:
        yield client
    else:
        yield SimpleNamespace(
            chat=SimpleNamespace(completions=_Completions(client.chat.completions, role)),
            beta=SimpleNamespace(chat=SimpleNamespace(
                completions=_Completions(client.beta.chat.completions, role))),
        )
