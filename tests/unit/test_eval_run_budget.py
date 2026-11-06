"""The run budget's real dispatch path: `factory.create_client` and
`build_structured_model` under `httpx.MockTransport`.

No paid calls — the transport is the mock. The proofs: every HTTP
attempt admits BEFORE it reaches the inner transport; the FINAL wire
body is what the bounds measure (a `beta.parse` schema expansion and
an `extra_body` merge cannot shrink the measured input or enlarge the
output ceiling); strict nonnegative-int usage reconciles, anything
indeterminate is terminal; a stream releases its reservation exactly
once.
"""

from __future__ import annotations

import json

import httpx
import openai
import pytest

from nora.agent import factory
from nora.agent.structured import build_structured_model
from nora.domain.run_budget import (
    RunBudget,
    RunBudgetError,
    active_run_budget,
)


def _sse(chunks):
    data = "".join("data: " + json.dumps(c) + "\n\n" for c in chunks)
    return data + "data: [DONE]\n\n"


def _chunk(content=None, finish=None, usage=None):
    chunk = {"id": "g", "object": "chat.completion.chunk",
             "model": "m",
             "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}
    if content:
        chunk["choices"][0]["delta"] = {"content": content}
    if usage:
        chunk["usage"] = usage
    return chunk


def _respond(requests, *, usage=None, chunks=None):
    async def respond(request):
        requests.append(json.loads(request.content))
        if chunks is not None:
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"},
                content=_sse(chunks))
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "ok"}}],
            "usage": usage or {"prompt_tokens": 11,
                               "completion_tokens": 4,
                               "total_tokens": 15},
        })
    return respond


def _client(requests, **kw):
    transport = httpx.MockTransport(_respond(requests, **kw))
    return factory.create_client(
        base_url="https://generated.invalid/v1",
        api_key="generated-key",
        http_client=httpx.AsyncClient(transport=transport))


async def test_admitted_call_counts_and_reconciles():
    requests: list[dict] = []
    budget = RunBudget(max_requests=1, max_input_bytes=10_000_000,
                       max_output_tokens=10_000, output_reserve=100)
    token = active_run_budget.set(budget)
    try:
        async with _client(requests) as client:
            await client.chat.completions.create(
                model="m", max_tokens=200, messages=[{"role": "user", "content": "hi"}])
            assert budget.requests == 1
            assert budget.input_tokens == 11
            assert budget.output_tokens == 4
            assert budget._reserved_out == 0   # released, exactly once
            with pytest.raises(RunBudgetError):
                await client.chat.completions.create(
                    model="m", max_tokens=200,
                    messages=[{"role": "user", "content": "again"}])
    finally:
        active_run_budget.reset(token)
    assert len(requests) == 1          # the refused call never left


async def test_extra_body_cannot_enlarge_the_ceiling():
    """An `extra_body` max_tokens override lands on the merged body the
    transport reads — 50000 declared under a 40000 cap refuses."""
    requests: list[dict] = []
    budget = RunBudget(max_requests=10, max_input_bytes=10_000_000,
                       max_output_tokens=40_000, output_reserve=4000)
    token = active_run_budget.set(budget)
    try:
        async with _client(requests) as client:
            with pytest.raises(openai.APIConnectionError):
                await client.chat.completions.create(
                    model="m", max_tokens=200, messages=[{"role": "user", "content": "hi"}],
                    extra_body={"max_tokens": 50_000})
        assert budget.spent is not None and budget.requests == 0
    finally:
        active_run_budget.reset(token)
    assert requests == []              # refused before the wire


async def test_schema_expansion_counts_the_final_wire_bytes():
    """`beta.parse` expands a Pydantic `response_format` to a full JSON
    schema — the transport measures THOSE bytes, not the argument
    object's repr. A >cap expansion refuses the attempt that carries
    it, not an estimate of the class name."""
    from pydantic import BaseModel, Field

    class Big(BaseModel):
        a: int = Field(description="x" * 30_000)

    requests: list[dict] = []
    budget = RunBudget(max_requests=10, max_input_bytes=10_000,
                       max_output_tokens=10_000, output_reserve=100)
    token = active_run_budget.set(budget)
    try:
        async with _client(requests) as client:
            with pytest.raises(openai.APIConnectionError):
                await client.beta.chat.completions.parse(
                    model="m", max_tokens=200,
                    messages=[{"role": "user", "content": "hi"}],
                    response_format=Big)
        assert budget.requests == 0      # the expanded body never left
    finally:
        active_run_budget.reset(token)
    assert requests == []              # the expanded body never left


async def test_negative_usage_is_terminal_never_credit():
    """Malformed usage (negative ints) is indeterminate: spent latched,
    the budget is not credited, `finished` reports invalid."""
    requests: list[dict] = []
    budget = RunBudget(max_requests=10, max_input_bytes=10_000_000,
                       max_output_tokens=10_000, output_reserve=100)
    token = active_run_budget.set(budget)
    try:
        async with _client(requests, usage={
                "prompt_tokens": -50, "completion_tokens": -30,
                "total_tokens": -80}) as client:
            with pytest.raises(openai.APIConnectionError):
                await client.chat.completions.create(
                    model="m", max_tokens=200,
                    messages=[{"role": "user", "content": "hi"}])
        assert budget.spent is not None
        assert budget.output_tokens >= 0     # never credited negative
        with pytest.raises(RunBudgetError):
            budget.finished()
    finally:
        active_run_budget.reset(token)


async def test_streams_release_the_reservation_exactly_once():
    """Nine tiny streams under the campaign envelope must not strand
    reservations — the tenth call still admits; `_reserved_out` returns
    to zero after every completion."""
    requests: list[dict] = []
    chunks = [_chunk(content="ok", finish="stop",
                     usage={"prompt_tokens": 1,
                            "completion_tokens": 1,
                            "total_tokens": 2})]
    budget = RunBudget(max_requests=40, max_input_bytes=10_000_000,
                       max_output_tokens=40_000, output_reserve=4000)
    token = active_run_budget.set(budget)
    try:
        async with _client(requests, chunks=chunks) as client:
            for _ in range(10):
                stream = await client.chat.completions.create(
                    model="m", max_tokens=200, stream=True,
                    stream_options={"include_usage": True},
                    messages=[{"role": "user", "content": "hi"}])
                async for _ in stream:
                    pass
                assert budget._reserved_out == 0
            assert budget.requests == 10
            assert budget.output_tokens == 10
    finally:
        active_run_budget.reset(token)


async def test_a_stream_without_usage_is_spent():
    requests: list[dict] = []
    chunks = [_chunk(content="ok", finish="stop")]   # no usage chunk
    budget = RunBudget(max_requests=10, max_input_bytes=10_000_000,
                       max_output_tokens=10_000, output_reserve=100)
    token = active_run_budget.set(budget)
    try:
        async with _client(requests, chunks=chunks) as client:
            stream = await client.chat.completions.create(
                model="m", max_tokens=200, stream=True,
                stream_options={"include_usage": True},
                messages=[{"role": "user", "content": "hi"}])
            with pytest.raises(RunBudgetError):
                async for _ in stream:
                    pass
            assert budget.spent is not None
            with pytest.raises(RunBudgetError):   # gated_create: spent
                await client.chat.completions.create(
                    model="m", max_tokens=200,
                    messages=[{"role": "user", "content": "hi"}])
    finally:
        active_run_budget.reset(token)
    assert len(requests) == 1          # the follow-up never left


async def test_deadline_refuses_dispatch():
    requests: list[dict] = []
    budget = RunBudget(max_requests=10, max_input_bytes=10_000,
                       max_output_tokens=10_000, output_reserve=10,
                       deadline=0.0)
    token = active_run_budget.set(budget)
    try:
        async with _client(requests) as client:
            with pytest.raises(RunBudgetError):
                await client.chat.completions.create(
                    model="m", max_tokens=200,
                    messages=[{"role": "user", "content": "hi"}])
    finally:
        active_run_budget.reset(token)
    assert requests == []


async def test_sdk_owned_provider_dispatches_through_the_budget():
    """`build_structured_model` hands the provider's internal
    `AsyncOpenAI` a bounded transport — a spent budget refuses its
    dispatch, a healthy one counts it. SDK-owned, not factory-built."""
    requests: list[dict] = []
    transport = httpx.MockTransport(_respond(requests))
    # a SPENT budget refuses before the wire — built under it, the way
    # composition builds the provider during a bounded run
    spent = RunBudget(max_requests=0, max_input_bytes=10_000,
                      max_output_tokens=10_000, output_reserve=100)
    token = active_run_budget.set(spent)
    try:
        model = build_structured_model(
            base_url="https://generated.invalid/v1",
            api_key="generated-key",
            model_id="m", max_tokens=100, temperature=0.0,
            http_client=httpx.AsyncClient(transport=transport))
        async with model._get_client() as client:   # the SDK-owned AsyncOpenAI
            with pytest.raises(openai.APIConnectionError):
                await client.chat.completions.create(
                    model="m", max_tokens=200,
                    messages=[{"role": "user", "content": "hi"}])
        assert spent.requests == 0 and requests == []
    finally:
        active_run_budget.reset(token)


def test_build_structured_model_leaves_no_budget_ungated():
    """Without an installed budget no http_client is injected — the
    provider keeps the SDK default transport, ungated by design."""
    model = build_structured_model(
        base_url="https://generated.invalid/v1", api_key="generated-key",
        model_id="m", max_tokens=100, temperature=0.0)
    assert model is not None           # constructed; dispatch ungated


def test_create_client_wraps_only_when_a_budget_is_installed():
    client = factory.create_client(base_url="https://x", api_key="k")
    transport = client._client._transport
    assert not hasattr(transport, "_budget")


@pytest.mark.anyio
async def test_a_strict_input_token_bound_refuses_before_any_http():
    """`max_input_tokens` asks for a bound nothing can measure
    pre-dispatch (provider template/framing is not a byte count): the
    budget marks itself unsupported and NO request reaches the
    transport — the run records unavailability, never a PASS."""
    requests: list[dict] = []
    budget = RunBudget(
        max_requests=10, max_input_tokens=600_000,
        max_output_tokens=40_000, output_reserve=4_000)
    token = active_run_budget.set(budget)
    try:
        transport = httpx.MockTransport(_respond(requests))
        client = factory.create_client(
            base_url="https://generated.invalid/v1", api_key="generated-key",
            http_client=httpx.AsyncClient(transport=transport))
        with pytest.raises(RunBudgetError, match="unsupported"):
            await client.chat.completions.create(
                model="m", max_tokens=200,
                messages=[{"role": "user", "content": "hi"}])
        assert requests == []                # zero transport attempts
        assert budget.requests == 0
        assert budget.spent is not None and "unsupported" in budget.spent
        with pytest.raises(RunBudgetError):   # serializes as a failure
            budget.finished()
    finally:
        active_run_budget.reset(token)


@pytest.mark.anyio
async def test_a_request_without_an_output_ceiling_refuses_before_http():
    """A final body with no positive max_tokens/max_completion_tokens
    declares an unbounded output — the budget refuses it BEFORE the
    inner transport rather than admit under a pretend reservation."""
    requests: list[dict] = []
    budget = RunBudget(max_requests=10, max_input_bytes=10**7,
                       max_output_tokens=40_000, output_reserve=4_000)
    token = active_run_budget.set(budget)
    try:
        transport = httpx.MockTransport(_respond(requests))
        client = factory.create_client(
            base_url="https://generated.invalid/v1", api_key="generated-key",
            http_client=httpx.AsyncClient(transport=transport))
        with pytest.raises(openai.APIConnectionError):
            await client.chat.completions.create(
                model="m",
                messages=[{"role": "user", "content": "hi"}])
        assert requests == []                    # zero transport attempts
        assert budget.requests == 0
        assert "ceiling" in (budget.spent or "")
    finally:
        active_run_budget.reset(token)


@pytest.mark.anyio
async def test_a_dual_ceiling_body_refuses_before_http():
    """max_tokens AND max_completion_tokens on the final wire is a
    contradictory declaration — the provider's own precedence is not
    ours to guess, so the budget refuses it before the inner transport
    on either order. One positive field alone is still admitted."""
    for dual in ({"max_tokens": 1, "max_completion_tokens": 50_000},
                 {"max_tokens": 50_000, "max_completion_tokens": 1}):
        # a FRESH budget+client per shape — the first refusal latches
        # spent, so sharing one would let the second shape pass on the
        # latched reason instead of its own field ordering
        requests: list[dict] = []
        budget = RunBudget(max_requests=10, max_input_bytes=10**7,
                           max_output_tokens=40_000, output_reserve=4_000)
        token = active_run_budget.set(budget)
        try:
            transport = httpx.MockTransport(_respond(requests))
            client = factory.create_client(
                base_url="https://generated.invalid/v1",
                api_key="generated-key",
                http_client=httpx.AsyncClient(transport=transport))
            with pytest.raises(
                    (RunBudgetError, openai.APIConnectionError)):
                await client.chat.completions.create(
                    model="m",
                    messages=[{"role": "user", "content": "hi"}],
                    extra_body=dual)
            assert requests == []
            assert budget.requests == 0
            assert "ceiling" in (budget.spent or "")
        finally:
            active_run_budget.reset(token)


@pytest.mark.anyio
async def test_a_fragmented_sse_stream_still_reconciles_at_done():
    """Real SSE can split `data:` payloads mid-line across chunks — the
    framed buffer must reassemble the terminal usage/[DONE] events so
    a real stream finishes and releases instead of falsely reporting
    missing usage."""
    body = b"data: " + json.dumps({
        "id": "g", "choices": [{"index": 0, "delta": {"content": "ok"},
                                "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                  "total_tokens": 2}}).encode() + b"\n\ndata: [DONE]\n\n"
    # fragment at every byte offset class: split the payload mid-`data:`

    requests: list[dict] = []

    async def respond(request):
        requests.append(json.loads(request.content))
        import httpx as _h
        # a response whose stream yields ~7-byte fragments
        class _Frag(_h.AsyncByteStream):
            def __init__(self, data: bytes) -> None:
                self._d = data
            async def __aiter__(self):
                i = 0
                while i < len(self._d):
                    yield self._d[i:i + 7]
                    i += 7
            async def aclose(self) -> None: ...
        return _h.Response(200, headers={"content-type": "text/event-stream"},
                           stream=_Frag(body))

    budget = RunBudget(max_requests=10, max_input_bytes=10**7,
                       max_output_tokens=40_000, output_reserve=4_000)
    token = active_run_budget.set(budget)
    try:
        client = factory.create_client(
            base_url="https://generated.invalid/v1", api_key="generated-key",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        stream = await client.chat.completions.create(
            model="m", max_tokens=200, stream=True,
            stream_options={"include_usage": True},
            messages=[{"role": "user", "content": "hi"}])
        async for _ in stream:
            pass
        assert budget.output_tokens == 1
        assert budget._reserved_out == 0
        assert budget.spent is None
    finally:
        active_run_budget.reset(token)


@pytest.mark.anyio
async def test_an_error_response_surfaces_its_status_not_a_fake_no_usage():
    """A non-2xx JSON error body has no usage — the latched reason must
    carry the real status/content-type/body diagnosis, not just a bare
    "no usage" that hides what the gateway actually said. A valid
    200-with-usage still reconciles (positive control)."""
    requests: list[dict] = []

    async def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            404, headers={"content-type": "application/json"},
            content=b'{"error": {"code": "model_not_found", "message": "no such id"}}')

    budget = RunBudget(max_requests=10, max_input_bytes=10**7,
                       max_output_tokens=40_000, output_reserve=4_000)
    token = active_run_budget.set(budget)
    try:
        client = factory.create_client(
            base_url="https://generated.invalid/v1", api_key="generated-key",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        with pytest.raises(
                (RunBudgetError, openai.APIConnectionError)):
            await client.chat.completions.create(
                model="m", max_tokens=200,
                messages=[{"role": "user", "content": "hi"}])
        assert "status=404" in (budget.spent or "")
        assert "model_not_found" in (budget.spent or "")
    finally:
        active_run_budget.reset(token)
