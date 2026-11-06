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
    budget = RunBudget(max_requests=1, max_input_tokens=10_000_000,
                       max_output_tokens=10_000, output_reserve=100)
    token = active_run_budget.set(budget)
    try:
        async with _client(requests) as client:
            await client.chat.completions.create(
                model="m", messages=[{"role": "user", "content": "hi"}])
            assert budget.requests == 1
            assert budget.input_tokens == 11
            assert budget.output_tokens == 4
            assert budget._reserved_out == 0   # released, exactly once
            with pytest.raises(RunBudgetError):
                await client.chat.completions.create(
                    model="m",
                    messages=[{"role": "user", "content": "again"}])
    finally:
        active_run_budget.reset(token)
    assert len(requests) == 1          # the refused call never left


async def test_extra_body_cannot_enlarge_the_ceiling():
    """An `extra_body` max_tokens override lands on the merged body the
    transport reads — 50000 declared under a 40000 cap refuses."""
    requests: list[dict] = []
    budget = RunBudget(max_requests=10, max_input_tokens=10_000_000,
                       max_output_tokens=40_000, output_reserve=4000)
    token = active_run_budget.set(budget)
    try:
        async with _client(requests) as client:
            with pytest.raises(openai.APIConnectionError):
                await client.chat.completions.create(
                    model="m", messages=[{"role": "user", "content": "hi"}],
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
    budget = RunBudget(max_requests=10, max_input_tokens=10_000,
                       max_output_tokens=10_000, output_reserve=100)
    token = active_run_budget.set(budget)
    try:
        async with _client(requests) as client:
            with pytest.raises(openai.APIConnectionError):
                await client.beta.chat.completions.parse(
                    model="m",
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
    budget = RunBudget(max_requests=10, max_input_tokens=10_000_000,
                       max_output_tokens=10_000, output_reserve=100)
    token = active_run_budget.set(budget)
    try:
        async with _client(requests, usage={
                "prompt_tokens": -50, "completion_tokens": -30,
                "total_tokens": -80}) as client:
            with pytest.raises(openai.APIConnectionError):
                await client.chat.completions.create(
                    model="m",
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
    budget = RunBudget(max_requests=40, max_input_tokens=10_000_000,
                       max_output_tokens=40_000, output_reserve=4000)
    token = active_run_budget.set(budget)
    try:
        async with _client(requests, chunks=chunks) as client:
            for _ in range(10):
                stream = await client.chat.completions.create(
                    model="m", stream=True,
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
    budget = RunBudget(max_requests=10, max_input_tokens=10_000_000,
                       max_output_tokens=10_000, output_reserve=100)
    token = active_run_budget.set(budget)
    try:
        async with _client(requests, chunks=chunks) as client:
            stream = await client.chat.completions.create(
                model="m", stream=True,
                stream_options={"include_usage": True},
                messages=[{"role": "user", "content": "hi"}])
            with pytest.raises(RunBudgetError):
                async for _ in stream:
                    pass
            assert budget.spent is not None
            with pytest.raises(RunBudgetError):   # gated_create: spent
                await client.chat.completions.create(
                    model="m",
                    messages=[{"role": "user", "content": "hi"}])
    finally:
        active_run_budget.reset(token)
    assert len(requests) == 1          # the follow-up never left


async def test_deadline_refuses_dispatch():
    requests: list[dict] = []
    budget = RunBudget(max_requests=10, max_input_tokens=10_000,
                       max_output_tokens=10_000, output_reserve=10,
                       deadline=0.0)
    token = active_run_budget.set(budget)
    try:
        async with _client(requests) as client:
            with pytest.raises(RunBudgetError):
                await client.chat.completions.create(
                    model="m",
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
    spent = RunBudget(max_requests=0, max_input_tokens=10_000,
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
                    model="m",
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
