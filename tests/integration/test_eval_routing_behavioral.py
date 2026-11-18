"""Anchored tool routing through the SDK and a local model-gateway listener."""

from __future__ import annotations

import json

import httpx
import pytest
from aiohttp import web
from opentelemetry import trace
from strands_evals import Case, Experiment

from evals import backbone
from evals.measurement import BEHAVIORAL
from evals.route_grader import ToolRouteGrader
from evals.services import load_services
from nora.agent import factory
from nora.domain.models import NormalizedMessage
from nora.domain.run_budget import RunBudget, RunBudgetError, active_run_budget
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.subject.eval_evidence import EvidenceError, RunEvidence
from nora.subject.obtain import default_policy
from nora.subject.recorded_services import Playback
from nora.subject.sandbox import eval_host_lock

NOW = 2_000_000_000_000
MODEL = "generated-routing-model"
IDENTITY = '[persons.generated]\nname = "Generated"\n'


def routing_plan(tmp_path, upstream, *, name, bindings, services):
    path = tmp_path / "routing-services.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "corpora": {
                    "tool_selection": {
                        name: {
                            "kind": "routing_recording",
                            "model_gateway": {"upstream": upstream, "models": [MODEL]},
                            "external": {"version": 1, "bindings": bindings, "services": services},
                        }
                    }
                },
            }
        )
    )
    return load_services(path).corpora["tool_selection"][name]


def _completion_response(body, name=None, args=None):
    call = {
        "id": f"generated-{name}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }
    message = (
        {"role": "assistant", "tool_calls": [call]}
        if name
        else {
            "role": "assistant",
            "content": "<debrief />",
        }
    )
    finish_reason = "tool_calls" if name else "stop"
    delta = {**message, "tool_calls": [{"index": 0, **call}]} if name else message
    model = body["model"]
    usage = {"prompt_tokens": 50, "completion_tokens": 5, "total_tokens": 55}
    if body.get("stream"):
        chunks = [
            {
                "id": "generated-chunk",
                "object": "chat.completion.chunk",
                "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            },
            {
                "id": "generated-chunk",
                "object": "chat.completion.chunk",
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
            },
            {
                "id": "generated-chunk",
                "object": "chat.completion.chunk",
                "model": model,
                "choices": [],
                "usage": usage,
            },
        ]
        text = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
        return web.Response(text=text + "data: [DONE]\n\n", content_type="text/event-stream")
    return web.json_response(
        {
            "id": "generated-completion",
            "object": "chat.completion",
            "model": model,
            "created": 1,
            "choices": [{"index": 0, "finish_reason": finish_reason, "message": message}],
            "usage": usage,
        }
    )


@pytest.fixture
async def routing_upstream(aiohttp_server):
    requests = []

    async def respond(request):
        body = await request.json()
        requests.append(body)
        if not body.get("tools"):
            return _completion_response(body)
        if not any(message.get("role") == "tool" for message in body["messages"]):
            return _completion_response(body, "calculate", {"expression": "2 + 2"})
        return _completion_response(body, "send_reply", {"text": "Four."})

    app = web.Application()
    app.router.add_post("/v1/chat/completions", respond)
    server = await aiohttp_server(app)
    return str(server.make_url("/v1")), requests


@pytest.fixture
def local_non_model_services(monkeypatch):
    original = Playback.applications

    async def bridge_action(request):
        action = await request.json()
        return web.json_response(
            {
                "action_id": action["action_id"],
                "state": "confirmed",
                "platform_ref": "generated-reply",
            }
        )

    async def memory(request):
        if request.method == "GET":
            return web.json_response({"card": []})
        body = await request.json()
        if request.path.endswith("/representation"):
            return web.json_response({"representation": ""})
        return web.json_response({"id": body.get("id", "generated-session")})

    def configure(service, app):
        if service == "bridge":

            async def bridge_read(request):
                return web.json_response(
                    {
                        "/v1/self": {"primary": "generated-self", "aliases": []},
                        "/v1/groups": [{"id": "g1", "members": [["+852111"]]}],
                        "/v1/health": {"ok": True},
                    }[request.path]
                )

            for route in ("/v1/self", "/v1/groups", "/v1/health"):
                app.router.add_get(route, bridge_read)
            app.router.add_post("/v1/actions", bridge_action)
        if service == "memory":
            app.router.add_get("/v3/workspaces/{workspace}/peers/{peer}/card", memory)
            for route in (
                "/v3/workspaces",
                "/v3/workspaces/{workspace}/peers",
                "/v3/workspaces/{workspace}/peers/{peer}/representation",
                "/v3/workspaces/{workspace}/sessions",
                "/v3/workspaces/{workspace}/sessions/{session}/peers",
                "/v3/workspaces/{workspace}/sessions/{session}/messages",
            ):
                app.router.add_post(route, memory)

    monkeypatch.setattr(
        Playback,
        "applications",
        lambda self: original(self, before=configure),
    )


async def _seed_anchor(clean_pg_url):
    engine = create_sql_engine(clean_pg_url)
    try:
        await SqlRecordStore(engine).append(
            message=NormalizedMessage(
                "signal",
                "g1",
                "group",
                "+852111",
                "Generated",
                NOW - 1000,
                "Generated earlier conversation",
                {},
            ),
            person_id="generated",
        )
    finally:
        await engine.dispose()


async def test_tool_selection_behavioral_route_crosses_the_bound_gateway(
    tmp_path,
    clean_pg_url,
    routing_upstream,
    monkeypatch,
    local_non_model_services,
):
    upstream, requests = routing_upstream
    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated")
    await _seed_anchor(clean_pg_url)
    from nora.subject.obtain import SubjectSpec, build_config

    overrides = {
        "platforms.signal.enabled": True,
        "platforms.signal.mode": "bridge",
        "relevance_gate.enabled": False,
        "participation.enabled": False,
        "llm.base_url": upstream,
        "llm.model": MODEL,
        "llm.defer_tools": False,
    }
    cfg = build_config(
        SubjectSpec(database_url=clean_pg_url, workdir=tmp_path / "config", overrides=overrides)
    )
    overrides.update({f"providers.{name}.base_url": upstream for name in cfg.providers})
    case = Case(
        name="generated-route",
        input="Nora, calculate two plus two.",
        metadata={
            "corpus": "tool_selection",
            "tools": ["calculate"],
            "mention": True,
            "person": "generated",
            "expect": "reply",
            "snapshot": {"platform": "signal", "conversation_id": "g1", "before_ms": NOW},
        },
    )
    recording = routing_plan(
        tmp_path,
        upstream,
        name=case.name,
        bindings={"platforms.signal.base_url": "bridge", "honcho.base_url": "memory"},
        services={"bridge": [], "memory": []},
    )
    spec = backbone.Spec(
        source_url=clean_pg_url,
        entry="tag",
        identity=IDENTITY + '[[persons.generated.signal]]\nphone = "+852111"\n',
        policy=default_policy(("signal", "g1")),
        overrides=overrides,
        services={case.name: recording},
        measurement=BEHAVIORAL,
    )
    backbone._prefill_capture()
    with eval_host_lock() as fd:
        evidence = RunEvidence(
            run_id="routing-test",
            lock_fd=fd,
            private_root=tmp_path / "evidence",
            metadata={},
        )
        budget = RunBudget(max_requests=3, max_output_tokens=50000)
        token = active_run_budget.set(budget)
        try:
            with evidence.capture(trace.get_tracer_provider()):
                report = await Experiment(
                    cases=[case], evaluators=[ToolRouteGrader()]
                ).run_evaluations_async(task=backbone.nora_task(spec), max_workers=1)
                evidence.exit_code = 0
        finally:
            active_run_budget.reset(token)
    verdict = report.detailed_results[0][0]
    assert verdict.label == "PASS", verdict.reason
    assert len(requests) == budget.requests == 3
    assert all(body["model"] == MODEL for body in requests)

    boundary = routing_plan(
        tmp_path,
        upstream,
        name=case.name,
        bindings={"honcho.base_url": "unused"},
        services={"unused": []},
    )
    boundary_budget = RunBudget(max_requests=1, max_output_tokens=10000)
    token = active_run_budget.set(boundary_budget)
    try:
        with pytest.raises(EvidenceError, match="model_identity_undeclared"):
            async with boundary.open("sqlite://"):
                async with httpx.AsyncClient(trust_env=False) as client:
                    with pytest.raises(PermissionError, match="not isolated"):
                        await client.post(f"{upstream}/chat/completions", json={"model": "other"})
        assert len(requests) == 3
        assert boundary_budget.requests == 0
        with pytest.raises(EvidenceError, match="external_dependency_not_isolated"):
            async with boundary.open("sqlite://"):
                async with factory.create_client(base_url=upstream, api_key="generated") as client:
                    kwargs = {
                        "model": MODEL,
                        "max_tokens": 64,
                        "messages": [{"role": "user", "content": "Generated"}],
                    }
                    await client.chat.completions.create(**kwargs)
                    with pytest.raises(RunBudgetError):
                        await client.chat.completions.create(**kwargs)
                async with httpx.AsyncClient(trust_env=False) as client:
                    with pytest.raises(PermissionError, match="not isolated"):
                        await client.get("http://127.0.0.1:1/unrelated")
    finally:
        active_run_budget.reset(token)
    assert len(requests) == 4


@pytest.mark.parametrize(
    ("called", "error", "verdict"),
    [
        (["calculate"], None, "PASS"),
        (["web_fetch"], None, "FAIL"),
        ([], "generated unfinished turn", "ERROR"),
    ],
)
def test_tool_selection_behavioral_receipt_preserves_route_verdicts(
    tmp_path,
    monkeypatch,
    capsys,
    called,
    error,
    verdict,
):
    import evals.__main__ as cli

    case = Case(name="generated-route", input="Generated", metadata={"tools": ["calculate"]})
    experiment = Experiment(cases=[case], evaluators=[ToolRouteGrader()])

    async def outcome(_case, _spec):
        return {
            "output": "",
            "environment_state": [
                backbone.EnvironmentState(name=backbone.NORA_TOOLS, state=called),
                backbone.EnvironmentState(name=backbone.NORA_ERROR, state=error),
            ],
        }

    monkeypatch.setattr(backbone, "_drive_uncaptured", outcome)
    monkeypatch.setattr(
        cli,
        "experiment_for",
        lambda *a, measurement, **k: (
            experiment,
            backbone.nora_task(backbone.Spec(source_url="", measurement=measurement)),
        ),
    )
    monkeypatch.setattr(cli, "census", lambda *_a, **_k: "generated case")
    backbone._prefill_capture()
    with eval_host_lock() as fd:
        evidence = RunEvidence(
            run_id="routing-receipt",
            lock_fd=fd,
            private_root=tmp_path / "evidence",
            metadata={},
        )
        with evidence.capture(trace.get_tracer_provider()):
            ok = cli._run_one(
                "tool_selection", source_url="", judge=None, sample=1, measurement=BEHAVIORAL
            )
            evidence.exit_code = 0 if ok else 1

    receipt = json.loads((evidence.path / "corpus-tool_selection.json").read_text())
    assert receipt["mode"] == "behavioral-evaluation"
    assert receipt["behavior"] == verdict
    assert receipt["outcome"]["behavior"]["verdicts"][verdict] == 1
    assert ok is (verdict == "PASS")
    assert f"model behavior: {verdict}" in capsys.readouterr().out
