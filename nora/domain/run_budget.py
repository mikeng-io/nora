"""A run-scoped bound on model dispatch, fail-closed by construction.

Admission lives at the transport: `_BoundedTransport` wraps the
`httpx` transport of every client a bounded run can reach — factory
clients, the judge's direct client, and SDK-owned provider clients
(`structured.build_structured_model` injects `bounded_http_client`
into the provider's `client_args`, so the `AsyncOpenAI` the SDK
builds internally dispatches through it too). Measuring at the wire
is the point: the transport sees the FINAL request body after every
SDK transformation — `response_format` schema expansion, `extra_body`
merges, default params — so the input bound and the output ceiling
apply to what actually leaves, not to Python-level arguments.

Per attempt, before the inner transport is called: the request count,
the campaign deadline, the serialized body's byte size against the
input bound, and the body's declared `max_tokens`/`max_completion_tokens`
(after every merge) against what the output cap still affords — an
`extra_body` override cannot enlarge the ceiling because the bound is
read from the merged body itself. Non-text message shapes and
non-JSON bodies refuse: the byte bound cannot speak for them.

Reconcile rides the response: non-stream JSON bodies yield `usage`;
streams are consumed through a wrapped iterator that bounds each
chunk by the remaining deadline and reconciles the terminal usage
chunk. Usage counts must be real nonnegative ints — `None`, a bool,
a negative or anything malformed is indeterminate, and indeterminate
is terminal: `spent` names it and every later admission refuses.
Each call's output reservation is released exactly once when its
response finishes reconciling, success or failure.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

import httpx


class RunBudgetError(RuntimeError):
    """A model dispatch was refused or a budget check failed terminally."""


#: The installed run budget, or None (production default: ungated).
active_run_budget: ContextVar[RunBudget | None] = ContextVar(
    "active_run_budget", default=None)


@dataclass
class RunBudget:
    """Counts and bounds dispatched model calls for one run.

    `admit` is the ONLY admission, one per HTTP attempt: request count,
    the final wire bytes against the input bound (a token can never
    cover fewer than one byte — a rigorous bound, never a tokenizer),
    the post-merge declared output ceiling, and the campaign deadline
    are all checked before the call reaches the inner transport.
    `reconcile` lands measured usage with strict nonnegative-int
    validation; anything indeterminate latches `spent`, which
    `finished` reports so a run can never return success over its
    bound.
    """

    max_requests: int
    max_input_tokens: int
    max_output_tokens: int
    deadline: float | None = None
    output_reserve: int = 0
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    input_bytes: int = 0
    _reserved_out: int = field(default=0, repr=False)
    _spent_reason: str | None = field(default=None, repr=False)
    monotonic: Callable[[], float] = time.monotonic

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

    def would_admit(self) -> None:
        """The non-mutating half of `admit`: spent, deadline and request
        count refuse BEFORE the SDK builds a request — the real
        admission still happens once at the transport."""
        if self._spent_reason is not None:
            raise RunBudgetError(self._spent_reason)
        left = self.remaining()
        if left is not None and left <= 0:
            raise self._fail("run budget: active-time cap reached")
        if self.requests + 1 > self.max_requests:
            raise self._fail(
                f"run budget: model requests {self.requests + 1} > "
                f"{self.max_requests}")

    def admit(self, *, input_bytes: int,
              declared_output_tokens: int) -> int:
        """Admit one HTTP attempt or refuse BEFORE dispatch.

        Returns the output reservation to release exactly once when the
        attempt's response finishes reconciling."""
        if self._spent_reason is not None:
            raise RunBudgetError(self._spent_reason)
        left = self.remaining()
        if left is not None and left <= 0:
            raise self._fail("run budget: active-time cap reached")
        if self.requests + 1 > self.max_requests:
            raise self._fail(
                f"run budget: model requests {self.requests + 1} > "
                f"{self.max_requests}")
        if self.input_bytes + input_bytes > self.max_input_tokens:
            raise self._fail(
                f"run budget: input size {self.input_bytes + input_bytes} "
                f"B > {self.max_input_tokens}")
        out = declared_output_tokens or self.output_reserve
        if out <= 0:
            raise self._fail("run budget: no output ceiling to enforce")
        if self.output_tokens + self._reserved_out + out > self.max_output_tokens:
            raise self._fail(
                f"run budget: reserved output {self._reserved_out + out} "
                f"> {self.max_output_tokens}")
        self.requests += 1
        self.input_bytes += input_bytes
        self._reserved_out += out
        return out

    def reconcile(self, *, input_tokens: int,
                  output_tokens: int) -> None:
        """Land the measured usage — strict nonnegative ints only."""
        for name, value in (("input", input_tokens), ("output", output_tokens)):
            if type(value) is not int or value < 0:
                raise self._fail(
                    f"run budget: invalid {name} usage {value!r} — "
                    "indeterminate usage is spent, never zero")
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

    def release(self, reservation: int) -> None:
        """Drop one attempt's output reservation, exactly once."""
        self._reserved_out = max(0, self._reserved_out - reservation)

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
            "input_bytes": self.input_bytes,
            "input_tokens_used": self.input_tokens,
            "output_tokens_used": self.output_tokens,
            "spent_reason": self._spent_reason,
        }


def _usage_counts(usage: Any) -> tuple[int, int] | None:
    """`prompt_tokens`/`completion_tokens` from a dict or usage object —
    strict ints or None when the shape is not there at all."""
    if usage is None:
        return None
    get: Any = (usage.get if isinstance(usage, dict)
                else lambda k, d=None: getattr(usage, k, d))
    inp, out = get("prompt_tokens"), get("completion_tokens")
    if inp is None and out is None:
        return None
    return (inp, out)  # strictness is reconcile's job, not extraction's


def _check_body_shape(body: dict[str, Any]) -> None:
    """A bounded run only admits text-shaped messages: `str` content or
    parts of `{"type": "text"}` — the only shapes the byte bound can
    cover. Checked on the FINAL body, after every SDK transform."""
    messages = body.get("messages")
    if messages is None:
        return
    if not isinstance(messages, list):
        raise RunBudgetError(
            "run budget: non-list messages — unsupported input shape")
    for msg in messages:
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


def _declared_ceiling(body: dict[str, Any]) -> int:
    """The request's own output bound read from the FINAL body — after
    `extra_body` merges, an override cannot enlarge the ceiling."""
    for key in ("max_tokens", "max_completion_tokens"):
        value = body.get(key)
        if type(value) is int and value > 0:
            return value
    return 0


class _BoundedStream:
    """An `aiter_bytes` source that bounds each chunk by the remaining
    deadline, reconciles terminal SSE usage, and releases the attempt's
    reservation exactly once on any ending — success or not."""

    def __init__(self, inner: Any, budget: RunBudget, reservation: int) -> None:
        self._inner, self._budget, self._reservation = inner, budget, reservation
        self._done = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        budget = self._budget
        usage: tuple[int, int] | None = None
        saw_done = False
        if hasattr(self._inner, "__aiter__"):
            it = self._inner.__aiter__()
        else:
            sync_it = iter(self._inner)

            async def _adapted() -> AsyncIterator[bytes]:
                for item in sync_it:
                    yield item
            it = _adapted()
        try:
            while True:
                left = budget.remaining()
                if left is not None and left <= 0:
                    raise budget._fail(
                        "run budget: deadline during stream")
                try:
                    if left is not None:
                        chunk = await asyncio.wait_for(it.__anext__(), left)
                    else:
                        chunk = await it.__anext__()
                except StopAsyncIteration:
                    break
                for line in chunk.split(b"data: ")[1:]:
                    line = line.strip()
                    if not line:
                        continue
                    if line == b"[DONE]":
                        saw_done = True
                        continue
                    try:
                        parsed = json.loads(line)
                    except ValueError:
                        continue
                    found = _usage_counts(parsed.get("usage"))
                    if found is not None:
                        usage = found
                if saw_done:
                    # The consumer may never ask for another chunk once it
                    # sees [DONE] in these bytes — reconcile and release
                    # BEFORE this yield, not after it.
                    if usage is None:
                        raise budget._fail(
                            "run budget: stream ended without usage")
                    budget.reconcile(
                        input_tokens=usage[0], output_tokens=usage[1])
                    self._release()
                yield chunk
                if saw_done:
                    return
            if usage is not None:
                budget.reconcile(input_tokens=usage[0],
                                 output_tokens=usage[1])
            else:
                raise budget._fail(
                    "run budget: stream ended without usage")
        except (asyncio.CancelledError, GeneratorExit):
            # A terminal-markered stream already reconciled and released —
            # the generator's own close afterwards is not a cancellation.
            if not self._done:
                budget.invalidate("run budget: stream cancelled")
            raise
        except RunBudgetError:
            raise
        except Exception as exc:
            budget.invalidate(
                f"run budget: stream failed: {type(exc).__name__}")
            raise
        finally:
            self._release()
            close = getattr(self._inner, "aclose", None)
            if close is not None:
                import contextlib
                with contextlib.suppress(Exception):
                    await close()

    def _release(self) -> None:
        if not self._done:
            self._done = True
            self._budget.release(self._reservation)

    async def aclose(self) -> None:
        # An early close with no terminal marker is indeterminate —
        # the usage it would have carried never landed. A close after
        # the terminal reconcile is ordinary cleanup.
        if not self._done:
            self._budget.invalidate(
                "run budget: stream closed before terminal usage")
        self._release()
        close = getattr(self._inner, "aclose", None)
        if close is not None:
            await close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _BoundedTransport(httpx.AsyncBaseTransport):
    """`httpx` transport wrapper: one admission per HTTP attempt,
    measured on the FINAL wire body; usage reconciled from the real
    response; every ending indeterminate is terminal."""

    def __init__(self, inner: Any, budget: RunBudget) -> None:
        self._inner, self._budget = inner, budget

    async def handle_async_request(self, request: Any) -> Any:
        budget = self._budget
        raw = request.content or b""
        try:
            body = json.loads(raw) if raw else {}
        except ValueError as exc:
            budget.invalidate("run budget: non-JSON request body")
            raise budget._fail(
                "run budget: non-JSON request body — unsupported shape"
            ) from exc
        _check_body_shape(body)
        reservation = budget.admit(
            input_bytes=len(raw),
            declared_output_tokens=_declared_ceiling(body))
        left = budget.remaining()
        try:
            if left is not None:
                response = await asyncio.wait_for(
                    self._inner.handle_async_request(request), left)
            else:
                response = await self._inner.handle_async_request(request)
        except (TimeoutError, asyncio.CancelledError):
            budget.release(reservation)
            budget.invalidate("run budget: dispatch timed out")
            raise
        except RunBudgetError:
            budget.release(reservation)
            raise
        except Exception:
            budget.release(reservation)
            budget.invalidate("run budget: dispatch failed")
            raise
        content_type = response.headers.get("content-type", "")
        if "event-stream" in content_type:
            stream = getattr(response, "stream", None)
            if stream is None:
                budget.release(reservation)
                budget.invalidate("run budget: stream without a body")
                raise RunBudgetError("run budget: stream without a body")
            import httpx

            class _Bound(httpx.AsyncByteStream):
                _wrap = _BoundedStream(stream, budget, reservation)

                async def __aiter__(self) -> AsyncIterator[bytes]:
                    async for chunk in self._wrap:
                        yield chunk

                async def aclose(self) -> None:
                    await self._wrap.aclose()

            try:
                # A fresh response carrying only the bounded stream: a
                # buffered `._content` would let `aiter_bytes` skip the
                # stream — and the reconcile — entirely.
                return httpx.Response(
                    status_code=response.status_code,
                    headers=response.headers,
                    stream=_Bound(),
                    request=request)
            except (AttributeError, TypeError) as exc:
                budget.release(reservation)
                budget.invalidate(
                    "run budget: unwrappable stream response")
                raise RunBudgetError(
                    "run budget: unwrappable stream response") from exc
        try:
            raw_body = await response.aread()
            try:
                parsed = json.loads(raw_body)
            except ValueError:
                parsed = None
            usage = _usage_counts(
                parsed.get("usage") if isinstance(parsed, dict) else None)
            if usage is None:
                raise budget._fail(
                    "run budget: response carried no usage")
            budget.reconcile(input_tokens=usage[0], output_tokens=usage[1])
        except RunBudgetError:
            raise
        except Exception as exc:
            budget.invalidate(
                f"run budget: response failed: {type(exc).__name__}")
            raise
        finally:
            budget.release(reservation)
        return response

    async def aclose(self) -> None:
        close = getattr(self._inner, "aclose", None)
        if close is not None:
            await close()

    async def __aenter__(self) -> Any:
        enter = getattr(self._inner, "__aenter__", None)
        if enter is not None:
            await enter()
        return self

    async def __aexit__(self, *args: Any) -> None:
        exit_ = getattr(self._inner, "__aexit__", None)
        if exit_ is not None:
            await exit_(*args)


#: The declared transport seam for SDK-owned clients a bounded run
#: must reach: when a provider builds its own `AsyncOpenAI` from
#: client_args (the structured/Standing path), `bounded_http_client`
#: wraps THIS client when the caller supplied none — the same
#: MockTransport-bearing client a test injects through
#: `factory.create_client(http_client=...)`, or None in production
#: (a real `AsyncHTTPTransport` is wrapped instead). Set by the run
#: that installed the budget; cleared with it.
injected_transport_client: ContextVar[Any] = ContextVar(
    "injected_transport_client", default=None)


def bounded_http_client(existing: Any = None) -> Any:
    """An `httpx.AsyncClient` dispatching through the installed budget.

    `existing` is the caller's own client; the `injected_transport_client`
    ContextVar covers callers that supplied none. A client already
    bounded to this budget is reused, never wrapped twice (a second
    wrap would double-count every attempt)."""
    budget = active_run_budget.get()
    if budget is None:
        return existing
    if existing is None:
        existing = injected_transport_client.get()
    import httpx
    inner = getattr(existing, "_transport", None) if existing else None
    if inner is not None and getattr(inner, "_budget", None) is budget:
        return existing
    if inner is None:
        inner = httpx.AsyncHTTPTransport()
    return httpx.AsyncClient(transport=_BoundedTransport(inner, budget))


async def gated_create(budget: RunBudget, create: Any,
                       *args: Any, **kwargs: Any) -> Any:
    """Pre-dispatch refusal for a wrapped resource: a spent budget or an
    expired deadline refuses BEFORE the SDK builds a request — the honest
    `RunBudgetError`, not an SDK-wrapped transport error. Counting, the
    wire-input bound and usage reconcile stay at the transport, which is
    the only authoritative measure; this layer never admits anything."""
    budget.would_admit()
    _check_body_shape(kwargs)
    return await create(*args, **kwargs)


def gate_client(client: Any) -> Any:
    """Bound an already-built AsyncOpenAI: wrap its transport so every
    HTTP attempt admits through the installed budget, and pin
    `max_retries=0` so one API call is one attempt (a client we cannot
    reach refuses — a bounded run must not dispatch ungated)."""
    budget = active_run_budget.get()
    if budget is None:
        return client
    import contextlib
    with contextlib.suppress(Exception):
        client.max_retries = 0
    inner_client = getattr(client, "_client", None)
    inner_transport = getattr(inner_client, "_transport", None)
    if inner_transport is None:
        raise RunBudgetError(
            "run budget: client transport unreachable — refusing "
            "to leave an SDK client ungated")
    if (getattr(inner_transport, "_budget", None) is not budget
            and inner_client is not None):
        inner_client._transport = _BoundedTransport(inner_transport, budget)
    import functools
    if budget is None:          # unreachable — the early return gates it
        return client           # (mypy narrowing for the partial below)
    for resource in (client.chat.completions, client.beta.chat.completions):
        for method in ("create", "parse"):
            fn = getattr(resource, method, None)
            if fn is not None:
                setattr(resource, method,
                        functools.partial(gated_create, budget, fn))
    return client
