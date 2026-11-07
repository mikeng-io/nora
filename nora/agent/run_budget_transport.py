"""The httpx layer of the run budget — the dispatch boundary.

`_BoundedTransport` admits every HTTP attempt on the FINAL wire body;
`_BoundedStream` reconciles terminal usage and releases the attempt's
reservation exactly once; `bounded_http_client` and `gate_client` put
them on the clients a bounded run reaches — factory clients, the
judge's direct client, and SDK-owned provider clients
(`build_structured_model` injects `bounded_http_client` into
`client_args`, so the `AsyncOpenAI` the SDK builds internally
dispatches through it too). Domain holds only the budget itself —
`nora.domain.run_budget` stays standard-library.
"""

from __future__ import annotations

import asyncio
import functools
import json
from collections.abc import AsyncIterator
from contextvars import ContextVar
from typing import Any

import httpx

from nora.domain.run_budget import (
    RunBudget,
    RunBudgetError,
    active_run_budget,
)


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


def _check_body_shape(body: dict[str, Any], budget: RunBudget) -> None:
    """A bounded run admits the request shapes it can honestly measure:
    `str` content, `{"type": "text"}` parts, and `{"type": "image_url"}`
    parts — the browser journey's screenshots. Image input is measured
    by the provider's own `usage` reconcile, so it is admitted only while
    the budget carries no hard input-TOKEN bound (a `max_input_tokens`
    budget is already `spent` at construction, so nothing dispatches
    under one anyway); the serialized-input byte bound still caps the
    wire body, image bytes included. Any other shape refuses AND LATCHES
    the budget — an unsupported shape that only raised would let the
    run's `finished` report success over a request the budget never
    understood. Checked on the FINAL body, after every SDK transform."""
    messages = body.get("messages")
    if messages is None:
        return
    if not isinstance(messages, list):
        raise budget._fail(
            "run budget: non-list messages — unsupported input shape")
    for msg in messages:
        if not isinstance(msg, dict):
            raise budget._fail(
                "run budget: non-dict message — unsupported input shape")
        content = msg.get("content")
        if content is None or isinstance(content, str):
            continue
        if isinstance(content, list) and all(
                isinstance(p, dict) and (
                    p.get("type") == "text"
                    or (p.get("type") == "image_url"
                        and budget.max_input_tokens is None
                        and isinstance(p.get("image_url"), dict)))
                for p in content):
            continue
        raise budget._fail(
            "run budget: unsupported message part — admitted shapes are "
            "text and, while input is measured not capped, image_url")


def _declared_ceiling(body: dict[str, Any]) -> int | None:
    """The request's own output bound read from the FINAL body, after
    `extra_body` merges. Both `max_tokens` and `max_completion_tokens`
    may be set; the provider's own precedence is not ours to guess —
    BOTH is a contradictory declaration the budget cannot bound, so it
    is refused (a `None` return means "no valid ceiling": unbounded or
    contradictory output is unsupported, refused before dispatch
    rather than admitted under a pretend reservation)."""
    ceilings = [v for k in ("max_tokens", "max_completion_tokens")
                if type(v := body.get(k)) is int and v > 0]
    return ceilings[0] if len(ceilings) == 1 else None


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
        pending = b""
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
                    if pending.strip():
                        for line in pending.splitlines():
                            if line.startswith(b"data:"):
                                line = line[len(b"data:"):].strip()
                                if line == b"[DONE]":
                                    saw_done = True
                    break
                pending += chunk
                while b"\n\n" in pending:
                    event, pending = pending.split(b"\n\n", 1)
                    for line in event.splitlines():
                        if not line.startswith(b"data:"):
                            continue
                        line = line[len(b"data:"):].strip()
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
        _check_body_shape(body, budget)
        ceiling = _declared_ceiling(body)
        if ceiling is None:
            budget.invalidate(
                "run budget: request declares no valid output ceiling")
            raise budget._fail(
                "run budget: request declares no valid output ceiling — "
                "max_tokens/max_completion_tokens must be a positive "
                "integer on the final wire body")
        reservation = budget.admit(
            input_bytes=len(raw),
            declared_output_tokens=ceiling)
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
            left = budget.remaining()
            if left is not None and left <= 0:
                raise budget._fail("run budget: deadline during response")
            if left is not None:
                raw_body = await asyncio.wait_for(response.aread(), left)
            else:
                raw_body = await response.aread()
            try:
                parsed = json.loads(raw_body)
            except ValueError:
                parsed = None
            usage = _usage_counts(
                parsed.get("usage") if isinstance(parsed, dict) else None)
            if usage is None:
                # a non-2xx/error or non-JSON body must surface its real
                # diagnosis in the latched reason — never pretend the
                # spend measured tokens it never received
                status = getattr(response, "status_code", "?")
                ctype = response.headers.get("content-type", "?")
                snippet = raw_body[:160].decode("utf-8", "replace")
                raise budget._fail(
                    "run budget: response carried no usage"
                    f" (status={status} content-type={ctype}"
                    f" body={snippet!r})")
            budget.reconcile(input_tokens=usage[0], output_tokens=usage[1])
        except RunBudgetError:
            raise
        except (TimeoutError, asyncio.CancelledError):
            budget.invalidate("run budget: response body timed out")
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
    _check_body_shape(kwargs, budget)
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
    if budget is None:          # unreachable — the early return gates it
        return client           # (mypy narrowing for the partial below)
    for resource in (client.chat.completions, client.beta.chat.completions):
        for method in ("create", "parse"):
            fn = getattr(resource, method, None)
            if fn is not None:
                setattr(resource, method,
                        functools.partial(gated_create, budget, fn))
    return client
