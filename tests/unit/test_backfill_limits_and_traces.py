"""Production backfill regressions, through CLI + real SDK with generated HTTP replies."""
from __future__ import annotations

import argparse
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
from nora.app import cli
from nora.app import unified_backfill as ub
from nora.app.cognition_agent_runner import LiveCognitionAgentRunner
from nora.app.cognition_session import AdaptorBudget
from nora.domain.stimuli import SessionLimitError
from tests.unit.test_unified_backfill import _FakeEngine, _patch_sql, _run_kwargs, _Runner, _s


@pytest.fixture
def exported(monkeypatch):
    exporter = InMemorySpanExporter()
    providers = []

    def install(provider):
        providers.append(provider)
        monkeypatch.setattr(trace, "_TRACER_PROVIDER", provider)

    def memory_exporter(self, **kwargs):
        self.tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))

    monkeypatch.setattr(telemetry.trace, "set_tracer_provider", install)
    monkeypatch.setattr(telemetry.StrandsTelemetry, "setup_otlp_exporter", memory_exporter)
    # Strands caches its tracer separately from the global OTel provider.
    import strands.telemetry.tracer as sdk_trace
    monkeypatch.setattr(sdk_trace, "_tracer_instance", None)
    yield exporter
    for provider in providers:
        provider.shutdown()


def model_runner(monkeypatch, actual_input=110, *, unknown=False, hang=False, started=None):
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
        delta = {"role": "assistant", "tool_calls": [{"index": 0, "id": "generated-call",
            "type": "function", "function": {"name": "read_more", "arguments": "{}"}}]}
        chunks = [{"id": "generated-response", "object": "chat.completion.chunk",
            "created": 1, "model": "generated-model",
            "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
            {"id": "generated-response", "object": "chat.completion.chunk", "created": 1,
             "model": "generated-model", "choices": [{"index": 0, "delta": {},
             "finish_reason": "tool_calls"}]}]
        if not unknown:
            chunks.append({"id": "generated-response", "object": "chat.completion.chunk",
                "created": 1, "model": "generated-model", "choices": [], "usage": {
                    "prompt_tokens": actual_input, "completion_tokens": 20,
                    "total_tokens": actual_input + 20}})
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
            text="".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n")

    client = AsyncOpenAI(api_key="generated-test-value", base_url="https://generated.invalid/v1",
        max_retries=0, http_client=httpx.AsyncClient(transport=httpx.MockTransport(answer)))
    model = factory.create_model_provider(model="generated-model", max_tokens=20,
        temperature=0, client=client, tool_choice="auto")

    async def count_tokens(messages, **kwargs):
        return 100 if not calls else 10

    monkeypatch.setattr(model, "count_tokens", count_tokens)
    runner = LiveCognitionAgentRunner(models=SimpleNamespace(create=lambda: model),
        retry=factory.RetryPolicy(max_attempts=1, initial_delay=0, max_delay=0))
    return runner, calls, client


async def read_more() -> dict:
    """Read another generated item."""
    return {"status": "ok", "text": "Generated evidence"}


async def run_model(runner, tokens):
    return await runner.run(system_prompt="Generated system", prompt="Generated input",
        tools=[read_more], budget=AdaptorBudget(turns=5, total_tokens=tokens,
            output_tokens=1000, timeout_seconds=10, reserve_tokens=True),
        trace_attributes={"cognition.session_id": "generated-session"})


@pytest.mark.parametrize("actual,cap,residual", [(100, 200, 0), (110, 130, 0), (125, 130, 15)])
async def test_run_reserves_each_call_and_pins_residual(monkeypatch, actual, cap, residual):
    live, calls, client = model_runner(monkeypatch, actual)
    metered = ub.MeteredAgentRunner(live)
    receipts = set()
    failures = []
    _patch_sql(monkeypatch, stimuli=[_s(1, 100), _s(2, 200)], pairs=receipts, fail_log=failures)
    runner = _Runner()

    async def replay(**kwargs):
        result = await run_model(metered, 10000)
        receipts.add(("stimuli:1", "h"))
        assert result.stop_reason == "limit_total_tokens"
        raise SessionLimitError(result.stop_reason)

    monkeypatch.setattr(runner, "replay", replay)
    kw = _run_kwargs()
    kw["bounds"] = ub.Bounds(max_batches=1, max_tokens=cap, max_seconds=30)
    try:
        result = await ub.run_units(_FakeEngine(object()), runner=runner,
            metered=metered, batch_stimuli=1, **kw)
    finally:
        await client.close()
    assert len(calls) == 1
    assert result["tokens"] == actual + 20
    assert result["tokens"] <= cap + residual
    assert max(0, result["tokens"] - cap) == residual
    assert result["usage_known"]
    assert failures == []
    assert result["units"]["u1"]["failures"] == 0


async def test_unaffordable_first_call_is_known_zero(monkeypatch):
    runner, calls, client = model_runner(monkeypatch)
    try:
        result = await run_model(runner, 129)
    finally:
        await client.close()
    assert result.stop_reason == "limit_total_tokens"
    assert result.total_tokens == 0
    assert calls == []


async def test_unknown_usage_stops_before_another_call(monkeypatch):
    runner, calls, client = model_runner(monkeypatch, unknown=True)
    try:
        with pytest.raises(Exception, match="usage unknown"):
            await run_model(runner, 1000)
    finally:
        await client.close()
    assert len(calls) == 1


@pytest.mark.parametrize("committed", [{1}, {1, 2}])
async def test_committed_inputs_survive_session_limit(monkeypatch, committed):
    receipts = set()
    failures = []
    _patch_sql(monkeypatch, stimuli=[_s(1, 100), _s(2, 200), _s(3, 300)],
        pairs=receipts, fail_log=failures)
    runner = _Runner()

    async def replay(**kwargs):
        receipts.update((f"input:u1:{sid}", "h") for sid in committed)
        raise SessionLimitError("limit_total_tokens")

    monkeypatch.setattr(runner, "replay", replay)
    kw = _run_kwargs()
    kw["bounds"] = ub.Bounds(max_batches=1, max_tokens=1000, max_seconds=30)
    result = await ub.run_units(_FakeEngine(object()), runner=runner, batch_stimuli=2, **kw)
    assert receipts
    assert failures == [("u1", sid) for sid in {1, 2} - committed]
    assert result["units"]["u1"]["stopped"] == ("unresolved" if committed == {1} else "max_batches")


async def test_cli_root_contains_session_and_generation_spans(
        monkeypatch, exported, example_config):
    import nora.app.composition as composition
    cfg = example_config.model_copy(update={"obs": example_config.obs.model_copy(update={
        "tracing": example_config.obs.tracing.model_copy(update={"enabled": True,
            "exporter": "otlp_http", "endpoint": "http://generated.invalid/traces"})})})
    monkeypatch.setattr(cli, "_config", lambda args: cfg)
    policy = SimpleNamespace(home_rooms=lambda: {}, rooms=lambda: [])
    monkeypatch.setattr(cli, "_policy", lambda cfg: policy)
    monkeypatch.setattr(composition, "social_person_rooms", lambda p: {})
    runner, calls, client = model_runner(monkeypatch)
    deps = SimpleNamespace(client=client, model_clients=[],
        registry=SimpleNamespace(self_aliases=lambda: ()))

    async def build(cfg):
        return deps

    async def dispose():
        pass

    monkeypatch.setattr(composition, "build_dependencies", build)
    engine = _FakeEngine(object())
    engine.dispose = dispose
    monkeypatch.setattr(cli, "create_sql_engine", lambda url: engine)
    monkeypatch.setattr(cli, "_database_url", lambda cfg: "generated")
    receipts = set()
    _patch_sql(monkeypatch, stimuli=[_s(1, 100)], pairs=receipts)
    lane = _Runner()
    metered = ub.MeteredAgentRunner(runner)

    async def replay(**kwargs):
        await run_model(metered, 200)
        receipts.add(("stimuli:1", "h"))

    monkeypatch.setattr(lane, "replay", replay)

    async def apply(engine, **kwargs):
        kw = _run_kwargs()
        kw["bounds"] = kwargs["bounds"]
        return await ub.run_units(engine, runner=lane, metered=metered,
            from_ms=10, until_ms=200, **kw)

    monkeypatch.setattr(ub, "apply", apply)
    args = argparse.Namespace(unit=["u1"], from_ms=10, until_ms=200, apply=True,
        max_batches=1, max_tokens=200, max_seconds=30, batch_stimuli=1)
    assert await cli.cmd_unified_backfill(args) == 0
    spans = exported.get_finished_spans()
    roots = [s for s in spans if s.parent is None]
    assert len(roots) == 1
    root = roots[0]
    assert root.name == "memory:unified-backfill"
    assert root.attributes["nora.backfill.max_tokens"] == 200
    assert root.attributes["nora.backfill.from_ms"] == 10
    assert root.attributes["nora.backfill.tokens"] == 130
    session, = [s for s in spans if s.name == "dream:cognition"]
    assert session.parent.span_id == root.context.span_id
    assert session.attributes["nora.cognition.tokens"] == 130
    generations = [s for s in spans if s.name == "chat"]
    assert len(generations) == len(calls) == 1
    assert generations[0].attributes["nora.budget.estimated_input_tokens"] == 100
    assert generations[0].attributes["gen_ai.usage.input_tokens"] == 110
    assert all(s.context.trace_id == root.context.trace_id for s in spans)
    measured, = [s for s in spans if "nora.budget.actual_input_tokens" in s.attributes]
    assert measured.attributes["nora.budget.actual_input_tokens"] == 110
    assert measured.attributes["nora.budget.estimated_input_tokens"] == 100
    assert measured.attributes["nora.budget.input_residual_tokens"] == 0



@pytest.mark.parametrize("after_commit", ["limit", "graph_failure"])
async def test_session_failure_receipts_exclude_committed_inputs(monkeypatch, after_commit):
    from nora.app.cognition_session import CognitionRunResult
    from tests.unit.cognition_rig import A, B, Script, session
    from tests.unit.test_cognition_session_runner import proposal, wake_memory

    memory = wake_memory()
    original_walk = memory.graph.walk

    async def walk(*args, **kwargs):
        if after_commit == "graph_failure" and memory.records.claims:
            raise RuntimeError("generated graph read failure after commit")
        return await original_walk(*args, **kwargs)

    monkeypatch.setattr(memory.graph, "walk", walk)

    async def behave(by, tools):
        await by["memory_propose"](**proposal())

    script = Script(behave)
    original_run = script.run

    async def run(**kwargs):
        await original_run(**kwargs)
        return CognitionRunResult(stop_reason="limit_total_tokens", total_tokens=100)

    monkeypatch.setattr(script, "run", run)
    with pytest.raises(Exception, match=r"limit_total_tokens|generated graph read failure"):
        await session(behave, rooms=(A, B), runner_kwargs={"memory": memory,
            "agent_runner": script})
    assert memory.records.claims  # the readings writer actually committed
    assert await memory.records.failures("u", 1) == 0
    assert await memory.records.failures("u", 3) == 1


async def test_historical_session_applies_reservation_before_first_call(monkeypatch):
    from dataclasses import replace

    from nora.domain.stimuli import UnitView
    from tests.unit.test_cognition_session_runner import NOW, _pending, _runner

    live, calls, client = model_runner(monkeypatch)
    runner, _, _ = _runner(agent_runner=live)
    runner = runner.with_collaborators(budget=replace(runner._budget, total_tokens=129))
    view = UnitView(unit="unit-1", pending=_pending("unit-1"),
        last_activity_ms=None, last_wake=None)
    try:
        with pytest.raises(SessionLimitError):
            await runner.replay(view=view, now_ms=NOW, historical=True)
        assert calls == []
        runner = runner.with_collaborators(budget=replace(runner._budget, total_tokens=130))
        with pytest.raises(SessionLimitError):
            await runner.replay(view=view, now_ms=NOW, historical=True)
        assert len(calls) == 1
    finally:
        await client.close()


async def test_cancelled_in_flight_call_has_unknown_spend(monkeypatch):
    from nora.app.cognition_session import SESSION_TOKENS, TokenSpend

    started = asyncio.Event()
    live, calls, client = model_runner(monkeypatch, hang=True, started=started)
    metered = ub.MeteredAgentRunner(live)
    spend = TokenSpend()
    token = SESSION_TOKENS.set(spend)
    try:
        task = asyncio.create_task(run_model(metered, 1000))
        await asyncio.wait_for(started.wait(), timeout=10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(calls) == 1
        assert spend.total is None
        assert metered.usage_known is False
    finally:
        SESSION_TOKENS.reset(token)
        await client.close()
