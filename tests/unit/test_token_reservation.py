"""Offline probes for the historical session's token reservation hook."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from openai import AsyncOpenAI
from opentelemetry import trace
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from nora.agent import factory, telemetry
from nora.agent.token_reservation import TokenReservation
from nora.app.cognition_agent_runner import DreamerAgentRunner
from nora.app.cognition_session import SESSION_TOKENS, AdaptorBudget, CognitionRunResult, TokenSpend
from nora.obs.metrics import Metrics
from nora.obs.tracing import build_sampler
from tests.fakes import dreamer_runtime_for
from tests.unit.test_cognition_session_runner import _runner


class _Model:
    def get_config(self):
        return {"params": {"max_tokens": 20}}


def _before(reservation: TokenReservation, *, estimate: int = 100):
    event = SimpleNamespace(
        cancel=False,
        projected_input_tokens=estimate,
        agent=SimpleNamespace(model=_Model(), trace_attributes={}),
    )
    reservation.before(event)
    return event


def test_an_unaffordable_first_call_is_refused_before_the_provider_call():
    reservation = TokenReservation(129)
    event = _before(reservation, estimate=100)
    assert event.cancel == "limit_total_tokens"
    assert reservation.stopped is True
    assert reservation.spent == 0


def test_unknown_usage_stops_the_session_instead_of_spending_against_an_unread_balance():
    reservation = TokenReservation(1000)
    event = _before(reservation, estimate=100)
    assert not event.cancel
    after = SimpleNamespace(
        stop_response=SimpleNamespace(message={"metadata": {"usage": {}}}),
        retry=True,
    )
    with pytest.raises(RuntimeError, match="usage unknown"):
        reservation.after(after)
    assert reservation.spent is None
    assert after.retry is False


def test_cancelled_in_flight_model_usage_remains_unknown():
    reservation = TokenReservation(1000)
    event = _before(reservation, estimate=100)
    assert not event.cancel
    assert reservation.spent is None


async def test_historical_sessions_enable_call_reservations():
    budgets = []

    class RecordingRunner:
        async def run(self, *, budget, **kwargs):
            budgets.append(budget)
            return CognitionRunResult(stop_reason="end_turn")

    runner, _store, _fake = _runner(agent_runner=RecordingRunner())
    kwargs = {
        "unit": "unit-1", "pending_ids": frozenset(), "system_prompt": "s", "prompt": "p",
        "tools": [], "trace_attributes": {},
    }
    await runner._run_agent(**kwargs, historical=False)
    await runner._run_agent(**kwargs, historical=True)
    assert [budget.reserve_tokens for budget in budgets] == [False, True]


@pytest.fixture
def exported(monkeypatch):
    """The generation spans the real SDK emits, captured at its own provider seam."""
    exporter = InMemorySpanExporter()
    providers = []

    def install(provider):
        providers.append(provider)
        monkeypatch.setattr(trace, "_TRACER_PROVIDER", provider)

    def memory_exporter(self, **kwargs):
        self.tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))

    monkeypatch.setattr(telemetry.trace, "set_tracer_provider", install)
    monkeypatch.setattr(telemetry.StrandsTelemetry, "setup_otlp_exporter", memory_exporter)
    import strands.telemetry.tracer as sdk_trace
    monkeypatch.setattr(sdk_trace, "_tracer_instance", None)
    yield exporter
    for provider in providers:
        provider.shutdown()


def _model_runner(
    monkeypatch, actual_input=125, actual_output=20, *, unknown=False, hang=False, started=None,
):
    """A generated OpenAI provider whose answer depends on the request it received."""
    calls = []

    async def answer(request):
        assert request.url.path == "/v1/chat/completions"
        body = json.loads(request.content)
        assert body["max_tokens"] == 20
        calls.append(body)
        if started is not None:
            started.set()
        if hang:
            await asyncio.sleep(30)
        follow_up = any(message.get("role") == "tool" for message in body["messages"])
        if follow_up:
            delta = {"role": "assistant", "content": "generated final answer"}
            finish = "stop"
        else:
            delta = {"role": "assistant", "tool_calls": [{"index": 0, "id": "generated-call",
                "type": "function", "function": {"name": "read_more", "arguments": "{}"}}]}
            finish = "tool_calls"
        chunks = [{"id": "generated-response", "object": "chat.completion.chunk",
            "created": 1, "model": "generated-model",
            "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
            {"id": "generated-response", "object": "chat.completion.chunk", "created": 1,
             "model": "generated-model", "choices": [{"index": 0, "delta": {},
             "finish_reason": finish}]}]
        if not unknown:
            chunks.append({"id": "generated-response", "object": "chat.completion.chunk",
                "created": 1, "model": "generated-model", "choices": [], "usage": {
                    "prompt_tokens": actual_input, "completion_tokens": actual_output,
                    "total_tokens": actual_input + actual_output}})
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
            text="".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n")

    client = AsyncOpenAI(api_key="generated-test-value", base_url="https://generated.invalid/v1",
        max_retries=0, http_client=httpx.AsyncClient(transport=httpx.MockTransport(answer)))
    model = factory.create_model_provider(model="generated-model", max_tokens=20,
        temperature=0, client=client, tool_choice="auto")

    async def count_tokens(messages, **kwargs):
        return 100 if not calls else 10

    monkeypatch.setattr(model, "count_tokens", count_tokens)
    runner = DreamerAgentRunner(runtime=dreamer_runtime_for(
        models=SimpleNamespace(create=lambda: model), metrics=Metrics(),
        retry=factory.RetryPolicy(max_attempts=1, initial_delay=0, max_delay=0)))
    return runner, calls, client


async def read_more() -> dict:
    """Read another generated item."""
    return {"status": "ok", "text": "Generated evidence"}


async def _run_model(runner, tokens):
    return await runner.run(system_prompt="Generated system", prompt="Generated input",
        tools=[read_more], budget=AdaptorBudget(turns=5, total_tokens=tokens,
            output_tokens=1000, timeout_seconds=10, reserve_tokens=True),
        trace_attributes={"cognition.session_id": "generated-session"})


def _enable_memory_tracing():
    telemetry.setup_agent_tracing(
        sampler=build_sampler(), service_name="nora", exporter="otlp_http",
        endpoint="http://generated.invalid/traces", headers_env="GENERATED_EMPTY",
    )


async def test_the_real_runner_admits_the_boundary_then_refuses_reconciled_usage(
    monkeypatch, exported,
):
    _enable_memory_tracing()
    runner, calls, client = _model_runner(monkeypatch, actual_input=115, actual_output=10)
    try:
        result = await _run_model(runner, 130)
    finally:
        await client.close()
    assert result.stop_reason == "limit_total_tokens"
    assert len(calls) == 1
    assert result.total_tokens == 125

    spans = exported.get_finished_spans()
    generations = [s for s in spans if s.name == "chat"]
    assert len(generations) == len(calls) == 1
    assert generations[0].attributes["nora.budget.estimated_input_tokens"] == 100
    assert generations[0].attributes["gen_ai.usage.input_tokens"] == 115
    measured, = [s for s in spans if "nora.budget.actual_input_tokens" in s.attributes]
    assert measured.attributes["nora.budget.actual_input_tokens"] == 115
    assert measured.attributes["nora.budget.estimated_input_tokens"] == 100
    assert measured.attributes["nora.budget.input_residual_tokens"] == 5
    assert measured.attributes["nora.budget.actual_output_tokens"] == 10
    assert measured.attributes["nora.budget.overshoot_tokens"] == 0
    session, = [s for s in spans if s.name == "dream:cognition"]
    assert all(s.context.trace_id == session.context.trace_id for s in spans)


async def test_the_real_runner_refuses_an_unaffordable_first_call_before_the_provider(
    monkeypatch,
):
    runner, calls, client = _model_runner(monkeypatch)
    try:
        result = await _run_model(runner, 129)
    finally:
        await client.close()
    assert result.stop_reason == "limit_total_tokens"
    assert result.total_tokens == 0
    assert calls == []


async def test_the_real_runner_stops_when_usage_is_unknown(monkeypatch):
    runner, calls, client = _model_runner(monkeypatch, unknown=True)
    try:
        with pytest.raises(Exception, match="usage unknown"):
            await _run_model(runner, 1000)
    finally:
        await client.close()
    assert len(calls) == 1


async def test_the_real_runner_hands_off_unknown_spend_when_cancelled(monkeypatch):
    started = asyncio.Event()
    runner, calls, client = _model_runner(monkeypatch, hang=True, started=started)
    spend = TokenSpend()
    token = SESSION_TOKENS.set(spend)
    try:
        task = asyncio.create_task(_run_model(runner, 1000))
        await asyncio.wait_for(started.wait(), timeout=10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(calls) == 1
        assert spend.total is None
    finally:
        SESSION_TOKENS.reset(token)
        await client.close()
