"""A run-scoped bound on model dispatch, fail-closed by construction.

`nora.domain.run_budget.gate_client` wraps an `AsyncOpenAI`'s
`chat.completions` AND `beta.chat.completions` methods — every model
request is `reserve`d before it leaves and `reconcile`d from its
measured usage, including streamed calls (usage rides the terminal
chunk; a stream that ends without it marks the budget spent, never a
zero). `factory.create_client` gates what it builds and the eval judge's
own client is gated by the same helper; bounded clients run with
`max_retries=0`, so one admission is one HTTP attempt.

Bounds enforced BEFORE dispatch: request count, the serialized request's
size as the conservative input bound (a token can never cover fewer
than one byte), the declared output ceiling (clamped onto requests that
omit one), and the remaining campaign deadline around the call AND full
stream consumption. Anything indeterminate — cancelled, timed out,
missing usage — is terminal: `spent` names it and every later reserve
refuses.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


class RunBudgetError(RuntimeError):
    """A model dispatch was refused or a budget check failed terminally."""


#: The installed run budget, or None (production default: ungated).
active_run_budget: ContextVar[RunBudget | None] = ContextVar(
    "active_run_budget", default=None)


@dataclass
class RunBudget:
    """Counts and bounds dispatched model calls for one run.

    `reserve` is the ONLY admission: request count, input size (the
    serialized request bytes — every token covers at least a byte, so it
    is a rigorous upper bound, never a tokenizer), declared output and
    the campaign deadline are all checked before the call. `reconcile`
    lands measured usage; a crossing marks `spent`, which `finished`
    reports so a run can never return success over its bound.
    """

    max_requests: int
    max_input_tokens: int
    max_output_tokens: int
    deadline: float | None = None
    output_reserve: int = 0
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    input_reserved: int = 0
    _reserved_out: int = field(default=0, repr=False)
    _spent_reason: str | None = field(default=None, repr=False)
    _in_call: int = field(default=0, repr=False)
    monotonic: Any = time.monotonic

    @property
    def spent(self) -> str | None:
        return self._spent_reason

    def _fail(self, reason: str) -> RunBudgetError:
        self._spent_reason = reason
        return RunBudgetError(reason)

    def remaining(self) -> float | None:
        """Seconds of campaign time left, or None when unbounded."""
        if self.deadline is None:
            return None
        return max(0.0, self.deadline - self.monotonic())

    def reserve(self, *, input_bytes: int,
                declared_output_tokens: int | None) -> int:
        """Admit one request or refuse BEFORE dispatch.

        Returns the output ceiling to enforce on the call (the declared
        value clamped to what remains, or `output_reserve` when the
        caller declared nothing)."""
        if self._spent_reason is not None:
            raise RunBudgetError(self._spent_reason)
        left = self.remaining()
        if left is not None and left <= 0:
            raise self._fail("run budget: active-time cap reached")
        if self.requests + 1 > self.max_requests:
            raise self._fail(
                f"run budget: model requests {self.requests + 1} > "
                f"{self.max_requests}")
        if self.input_reserved + input_bytes > self.max_input_tokens:
            raise self._fail(
                f"run budget: input size {self.input_reserved + input_bytes} "
                f"B > {self.max_input_tokens}")
        out = declared_output_tokens or self.output_reserve
        if out <= 0:
            raise self._fail("run budget: no output ceiling to enforce")
        if self.output_tokens + self._reserved_out + out > self.max_output_tokens:
            raise self._fail(
                f"run budget: reserved output {self._reserved_out + out} "
                f"> {self.max_output_tokens}")
        self.requests += 1
        self.input_reserved += input_bytes
        self._reserved_out += out
        return out

    def reconcile(self, *, input_tokens: int | None,
                  output_tokens: int | None) -> None:
        """Land the measured usage; None is unknown and spent."""
        if input_tokens is None or output_tokens is None:
            raise self._fail("run budget: response carried no usage")
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        if self.input_tokens > self.max_input_tokens:
            raise self._fail(
                f"run budget: input tokens {self.input_tokens} > "
                f"{self.max_input_tokens}")
        if self.output_tokens > self.max_output_tokens:
            raise self._fail(
                f"run budget: output tokens {self.output_tokens} > "
                f"{self.max_output_tokens}")

    def release(self, out_ceiling: int) -> None:
        """Drop an unused reservation after a reconciled call."""
        self._reserved_out = max(0, self._reserved_out - out_ceiling)

    def invalidate(self, reason: str) -> None:
        """A dispatch ended indeterminate (cancel/timeout): spent."""
        if self._spent_reason is None:
            self._spent_reason = reason

    def finished(self) -> None:
        """Terminal check: anything invalid makes the run ERROR."""
        if self._spent_reason is not None:
            raise RunBudgetError(self._spent_reason)

    def as_metadata(self) -> dict[str, Any]:
        return {
            "max_requests": self.max_requests,
            "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "deadline": self.deadline,
            "output_reserve": self.output_reserve,
            "requests_used": self.requests,
            "input_reserved": self.input_reserved,
            "input_tokens_used": self.input_tokens,
            "output_tokens_used": self.output_tokens,
            "spent_reason": self._spent_reason,
        }


def declared_output(kwargs: dict[str, Any]) -> int | None:
    """The request's own output bound, whichever field the caller set."""
    for key in ("max_tokens", "max_completion_tokens"):
        value = kwargs.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return None


def _request_bytes(args: tuple, kwargs: dict[str, Any]) -> int:
    """The serialized request's size — the conservative input bound.

    Every token covers at least one byte, so the byte count is a rigorous
    upper bound on input tokens, never a tokenizer."""
    try:
        return len(json.dumps((args, kwargs), default=str).encode())
    except (TypeError, ValueError):
        return sum(len(str(a)) for a in args) + sum(
            len(str(k)) + len(str(v)) for k, v in kwargs.items())


def _usage_of(response: Any) -> tuple[int | None, int | None]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None, None
    return (getattr(usage, "prompt_tokens", None),
            getattr(usage, "completion_tokens", None))


def gate_client(client: Any) -> Any:
    """Wrap an AsyncOpenAI's chat + beta completions with the installed
    budget, and bound its internal retries to the admission (bounded
    runs get `max_retries=0` — one admission is one HTTP attempt)."""
    budget = active_run_budget.get()
    if budget is None:
        return client
    import contextlib
    with contextlib.suppress(Exception):
        client.max_retries = 0
    import functools
    for resource in (client.chat.completions, client.beta.chat.completions):
        for method in ("create", "parse"):
            fn = getattr(resource, method, None)
            if fn is not None:
                setattr(resource, method,
                        functools.partial(gated_create, budget, fn))
    return client


async def _bounded_stream(budget: RunBudget, stream: Any,
                          ceiling: int) -> AsyncIterator[Any]:
    """Consume a chat stream under the budget: terminal usage reconciled,
    deadline bounded per chunk, anything indeterminate spent."""
    saw_usage = False
    it = stream.__aiter__()
    try:
        while True:
            left = budget.remaining()
            if left is not None and left <= 0:
                raise budget._fail("run budget: deadline during stream")
            try:
                if left is not None:
                    chunk = await asyncio.wait_for(it.__anext__(), left)
                else:
                    chunk = await it.__anext__()
            except StopAsyncIteration:
                break
            u = getattr(chunk, "usage", None)
            if u is not None:
                saw_usage = True
                budget.reconcile(
                    input_tokens=getattr(u, "prompt_tokens", None),
                    output_tokens=getattr(u, "completion_tokens", None))
            yield chunk
        if not saw_usage:
            raise budget._fail("run budget: stream ended without usage")
    except (asyncio.CancelledError, GeneratorExit):
        budget.invalidate("run budget: stream cancelled")
        raise
    except RunBudgetError:
        budget.release(ceiling)
        raise
    except Exception as exc:
        budget.invalidate(f"run budget: stream failed: {type(exc).__name__}")
        raise
    finally:
        close = getattr(stream, "aclose", None)
        if close is not None:
            import contextlib
            with contextlib.suppress(Exception):
                await close()


def _check_input_shape(kwargs: dict[str, Any]) -> None:
    """A bounded run only admits text-shaped messages: `str` content or
    parts of `{"type": "text"}` — the only shape the serialized-byte
    input bound is valid for. Image/audio/other parts, or a message the
    bound cannot speak for, refuse BEFORE dispatch."""
    for msg in kwargs.get("messages") or ():
        if not isinstance(msg, dict):
            raise RunBudgetError(
                "run budget: non-dict message — unsupported input shape")
        content = msg.get("content")
        if content is None or isinstance(content, str):
            continue
        if isinstance(content, list) and all(
                isinstance(p, dict) and p.get("type") == "text"
                for p in content):
            continue
        raise RunBudgetError(
            "run budget: non-text message part — the byte bound cannot "
            "cover image/audio/other input shapes")


async def gated_create(budget: RunBudget, create: Any,
                       *args: Any, **kwargs: Any) -> Any:
    """`chat.completions.create`/`parse` under a run budget."""
    if budget._in_call:
        return await create(*args, **kwargs)  # parse->create: one admission
    _check_input_shape(kwargs)
    ceiling = budget.reserve(
        input_bytes=_request_bytes(args, kwargs),
        declared_output_tokens=declared_output(kwargs))
    if declared_output(kwargs) is None and ceiling:
        kwargs["max_tokens"] = ceiling
    left = budget.remaining()
    budget._in_call += 1
    try:
        if left is not None:
            response = await asyncio.wait_for(create(*args, **kwargs), left)
        else:
            response = await create(*args, **kwargs)
    except (TimeoutError, asyncio.CancelledError) as exc:
        budget.invalidate(
            f"run budget: dispatch {type(exc).__name__}")
        raise
    except Exception as exc:
        budget.invalidate(
            f"run budget: dispatch failed: {type(exc).__name__}")
        raise
    finally:
        budget._in_call = max(0, budget._in_call - 1)
    if hasattr(response, "__aiter__"):
        # A stream: reservation held through full consumption; terminal
        # usage reconciles it — no stream ever reads as zero.
        return _bounded_stream(budget, response, ceiling)
    inp, out = _usage_of(response)
    try:
        budget.reconcile(input_tokens=inp, output_tokens=out)
    finally:
        budget.release(ceiling)
    return response
