"""A declared generated browser in the ordinary anchored routing subject."""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path

import httpx
import pytest
from aiohttp import web
from opentelemetry import trace
from strands_evals import Experiment

from evals import backbone
from evals.measurement import BEHAVIORAL
from evals.scenarios import experiment_for
from evals.services import load_services
from nora.domain.computer import computer_unit_scope
from nora.domain.computer_vms import FileScopeEndpoints
from nora.domain.run_budget import RunBudget, active_run_budget
from nora.infra.computer.browser import connect_browser_cdp
from nora.subject.eval_evidence import EvidenceError, RunEvidence, read_events
from nora.subject.obtain import SubjectSpec, build_config, default_policy
from nora.subject.routing_browser_fixture import BROWSER_BINDING, OFFER_URL
from nora.subject.sandbox import eval_host_lock
from tests.integration.test_eval_routing_behavioral import (
    IDENTITY,
    MODEL,
    NOW,
    _completion_response,
    _note_evidence_failure,
    _seed_anchor,
)

_CASE = "route_web_browse_interactive_evidence"


def _services(tmp_path, upstream, *, rows=None):
    path = tmp_path / "browser-services.json"
    bindings = {"honcho.base_url": "memory"}
    if rows is None:
        rows = {"memory": []}
    else:
        bindings["platforms.signal.base_url"] = "bridge"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "corpora": {
                    "tool_selection": {
                        _CASE: {
                            "kind": "routing_recording",
                            "model_gateway": {"upstream": upstream, "models": [MODEL]},
                            "browser_fixture": "offer_lookup",
                            "external": {"version": 1, "bindings": bindings, "services": rows},
                        }
                    }
                },
            }
        )
    )
    return load_services(path).corpora["tool_selection"]


def _recordings():
    def row(method, path, value, *, repeatable=False):
        return {
            "method": method,
            "path": path,
            "repeatable": repeatable,
            "response": {"status": 200, "json": value},
        }

    delivered = {"state": "confirmed", "platform_ref": "generated-reply"}
    workspace = "/v3/workspaces/nora"
    session = f"{workspace}/sessions/nora-conv-g1"
    return {
        "bridge": [
            row("GET", "/v1/self", {"primary": "generated-self", "aliases": []}, repeatable=True),
            row("POST", "/v1/actions", delivered),
            row("GET", "/v1/groups", [{"id": "g1", "members": [["+852111"]]}], repeatable=True),
            row("GET", "/v1/health", {"ok": True}, repeatable=True),
            *[row("POST", "/v1/actions", delivered) for _ in range(3)],
        ],
        "memory": [
            row("POST", "/v3/workspaces", {"id": "nora"}),
            row("POST", f"{workspace}/peers", {"id": "nora"}),
            row("POST", f"{workspace}/peers", {"id": "generated"}),
            row("GET", f"{workspace}/peers/generated/card", {"card": []}),
            row("POST", f"{workspace}/peers/nora/representation", {"representation": ""}),
            row("POST", f"{workspace}/sessions", {"id": "nora-conv-g1"}),
            *[row("POST", f"{session}/peers", {"id": "generated-session"}) for _ in range(2)],
            *[row("POST", f"{session}/messages", {"id": "generated-session"}) for _ in range(2)],
        ],
    }


@pytest.fixture
async def browser_upstream(aiohttp_server):
    requests = []
    choices = iter(("Click Show offers", "Select Wan Chai in Branch", "Click Check stock", None))

    async def respond(request):
        body = await request.json()
        requests.append(body)
        text = body["messages"][-1].get("content", "")
        if isinstance(text, list):
            text = "".join(part.get("text", "") for part in text)
        admitted = {tool["function"]["name"] for tool in body.get("tools", [])}
        if "web_browse" in admitted and not any(m["role"] == "tool" for m in body["messages"]):
            return _completion_response(
                body,
                "web_browse",
                {
                    "objective": "Check whether the Wan Chai branch page publishes stock.",
                    "start_url": OFFER_URL,
                },
            )
        if admitted:
            return _completion_response(body, "send_reply", {"text": "Generated answer."})
        if "<web_retrieval " not in text:
            return _completion_response(body)
        if body["messages"][0]["content"].startswith("Answer the objective"):
            assert "HKD 399. Stock not published." in text
            content = json.dumps(
                {
                    "found": True,
                    "answer": "EX4 is HKD 399 at Wan Chai. Stock is unpublished and unconfirmed.",
                }
            )
        elif (label := next(choices)) is not None:
            match = re.search(r'"([0-9]+-[0-9]+)": "' + re.escape(label) + '"', text)
            assert match is not None, text
            content = match[1]
        else:
            assert "HKD 399. Stock not published." in text
            match = re.search(r'"([0-9]+-finish)"', text)
            assert match is not None, text
            content = match[1]
        return web.json_response(
            {
                "id": "generated-browse",
                "object": "chat.completion",
                "model": body["model"],
                "created": 1,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": content},
                    }
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25},
            }
        )

    app = web.Application()
    app.router.add_post("/v1/chat/completions", respond)
    server = await aiohttp_server(app)
    return str(server.make_url("/v1")), requests


@pytest.fixture
def generated_deployment(tmp_path, monkeypatch):
    identity = IDENTITY + '[[persons.generated.signal]]\nphone = "+852111"\n'
    (tmp_path / "identity.toml").write_text(identity)
    (tmp_path / "policy.toml").write_text(default_policy(("signal", "g1")))
    monkeypatch.setattr(backbone, "_DEPLOYMENT_DIR", tmp_path)
    backbone._deployment_identity.cache_clear()
    backbone._deployment_policy.cache_clear()
    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated")
    monkeypatch.setenv(
        "NORA_MEDIA_KEY",
        base64.b64encode(b"generated-media-key-32byte-pad!!").decode(),
    )
    yield
    backbone._deployment_identity.cache_clear()
    backbone._deployment_policy.cache_clear()


async def test_anchored_browser_fixture_executes_web_browse(
    tmp_path,
    clean_pg_url,
    browser_upstream,
    generated_deployment,
):
    upstream, requests = browser_upstream
    await _seed_anchor(clean_pg_url)
    overrides = {
        "platforms.signal.enabled": True,
        "platforms.signal.mode": "bridge",
        "relevance_gate.enabled": False,
        "participation.enabled": False,
        "llm.base_url": upstream,
        "llm.model": MODEL,
        "llm.defer_tools": False,
        "obs.metrics.port": 0,
        "media.encryption_key_env": "NORA_MEDIA_KEY",
        "computer.enabled": True,
        "computer.browse.enabled": True,
        "computer.scoped.enabled": True,
        "computer.scoped.browser_sweep_seconds": 7200.0,
        "computer.sessions.sweep_interval_seconds": 7200.0,
    }
    cfg = build_config(
        SubjectSpec(
            database_url=clean_pg_url,
            workdir=tmp_path / "config",
            overrides=overrides,
        )
    )
    overrides.update({f"providers.{name}.base_url": upstream for name in cfg.providers})
    experiment, task = experiment_for(
        "tool_selection",
        source_url=clean_pg_url,
        only=frozenset({_CASE}),
        services=_services(tmp_path, upstream, rows=_recordings()),
        overrides=overrides,
        measurement=BEHAVIORAL,
    )
    original = experiment.cases[0]
    case = original.model_copy(
        update={
            "metadata": {
                **original.metadata,
                "person": "generated",
                "snapshot": {"platform": "signal", "conversation_id": "g1", "before_ms": NOW},
            }
        }
    )
    experiment = Experiment(cases=[case], evaluators=experiment.evaluators)
    backbone._prefill_capture()
    report = None
    with eval_host_lock() as fd:
        evidence = RunEvidence(
            run_id="generated-browser-routing",
            lock_fd=fd,
            private_root=tmp_path / "evidence",
            metadata={},
        )
        budget = RunBudget(max_requests=10, max_output_tokens=50000)
        token = active_run_budget.set(budget)
        try:
            with evidence.capture(trace.get_tracer_provider()):
                report = await experiment.run_evaluations_async(task=task, max_workers=1)
                evidence.exit_code = 0
        except EvidenceError as exc:
            _note_evidence_failure(exc, evidence, report)
            raise
        finally:
            active_run_budget.reset(token)
    verdict = report.detailed_results[0][0]
    assert verdict.label == "PASS", verdict.reason
    events = read_events(evidence.path)
    outcomes = [event for event in events if event["kind"] == "case_result"]
    assert len(outcomes) == 1
    assert not any(event["kind"] == "external_service_mismatch" for event in events)
    state = {field["name"]: field["state"] for field in outcomes[0]["result"]["environment_state"]}
    assert state["nora.error"] is None
    results = dict(state["nora.tool_results"])
    result = json.loads(json.loads(results["web_browse"])[0]["text"])
    assert result["status"] == "ok"
    assert result["url"] == OFFER_URL
    assert "HKD 399" in result["answer"]
    assert "unpublished and unconfirmed" in result["answer"]
    assert budget.requests == len(requests) == 8


@pytest.mark.parametrize("abort", [False, True])
async def test_routing_browser_fixture_is_case_owned(tmp_path, aiohttp_server, abort):
    unowned = await aiohttp_server(web.Application())
    upstream = await aiohttp_server(web.Application())
    upstream_url = str(upstream.make_url("/v1"))
    recording = _services(tmp_path, upstream_url)[_CASE]
    cfg = build_config(
        SubjectSpec(
            database_url="sqlite://",
            workdir=tmp_path / "config",
            overrides={"llm.base_url": upstream_url},
        )
    )
    scope = computer_unit_scope("nora")
    abort_scope = computer_unit_scope("different-unit")

    async def exercise():
        nonlocal endpoint, registry
        async with recording.open("sqlite://") as environment:
            bound = environment.bind_config(cfg)
            registry = Path(bound.computer.scoped.registry)
            endpoints = FileScopeEndpoints(registry)
            assert await endpoints.registered() == (scope,)
            record = await endpoints.resolve(scope)
            assert record is not None
            assert record.base_url == environment.urls[BROWSER_BINDING]
            endpoint = record.base_url
            (registry / f"{abort_scope}.json").write_text(
                (registry / f"{scope}.json").read_text(),
            )
            assert await endpoints.resolve(abort_scope) is None
            kwargs = {
                "token": bound.credentials[bound.computer.token_env],
                "scope": scope,
                "instance": record.instance,
                "timeout_seconds": 2,
            }
            async with connect_browser_cdp(base_url=endpoint, **kwargs) as cdp:
                assert await cdp.call("Target.getTargets") == {"targetInfos": []}
            assert environment.guarded
            if abort:
                async with httpx.AsyncClient(trust_env=False) as client:
                    with pytest.raises(PermissionError, match="not isolated"):
                        await client.get(str(unowned.make_url("/")))
                environment.check()

    endpoint = None
    registry = None
    if abort:
        with pytest.raises(EvidenceError, match="external_dependency_not_isolated"):
            await exercise()
    else:
        await exercise()
    assert registry is not None and not registry.exists()
    assert endpoint is not None
    async with httpx.AsyncClient(trust_env=False) as client:
        with pytest.raises(httpx.ConnectError):
            await client.get(endpoint)
