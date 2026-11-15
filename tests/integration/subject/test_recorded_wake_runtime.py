"""Generated HTTP responses drive the real session, settlement, evidence and judges."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from aiohttp import web
from opentelemetry import trace

from nora.domain.models import NormalizedMessage
from nora.domain.stimuli import NewStimulus
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.schema import COGNITION_WAKE_OUTCOME
from nora.infra.sql.stimuli import SqlStimulusStore
from nora.subject.environment import IsolatedServices
from nora.subject.eval_evidence import RunEvidence, read_events, validate_events
from nora.subject.obtain import SubjectSpec, build_config, default_policy
from nora.subject.sandbox import eval_host_lock
from scripts.cognition_loop.cases import Case, Expectation
from scripts.cognition_loop.run import _run_wake
from scripts.memory_graph_spike import Judges, measured_run_budget
from tests.integration.subject.test_replay_service_scope import NOW
from tests.integration.test_the_subject_is_the_deployed_graph import _empty_honcho_app

MODELS = ["generated-a", "generated-b"]


def completion(model, content, *, stream=False):
    response = {"id": "generated-response", "object": "chat.completion", "created": 1,
                "model": model, "choices": [{"index": 0, "finish_reason": "stop",
                    "message": {"role": "assistant", "content": content}}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}}
    result = {"status": 200, "headers": {"x-omniroute-model": model + "-served"}}
    if stream:
        chunks = [{**response, "object": "chat.completion.chunk", "choices": [{
            "index": 0, "delta": {"role": "assistant", "content": content},
            "finish_reason": None}]}, {**response, "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}]
        result["headers"]["content-type"] = "text/event-stream"
        result["chunks"] = chunks
    else:
        result["json"] = response
    return {"method": "POST", "path": "/v1/chat/completions", "response": result}


def services(cfg, requests):
    rows = [completion(cfg.dreamer.model, "Generated terminal response.", stream=True),
            completion(cfg.llm.model, json.dumps({"items": []}), stream=True),
            *(completion(alias, json.dumps({"verdict": "met", "reason": "Generated wire control",
                                           "entry_ids": []})) for alias in MODELS)]
    model = web.Application()

    async def respond(request):
        body = await request.json()
        index = len(requests)
        requests.append(body)
        if index >= len(rows):
            return web.json_response({"error": "unexpected model call"}, status=422)
        response = rows[index]["response"]
        payload = response.get("json") or response["chunks"][0]
        if (body["model"] != payload["model"]
                or bool(body.get("stream")) != ("chunks" in response)):
            return web.json_response({"error": "unexpected model route"}, status=422)
        if "json" in response:
            return web.json_response(response["json"], headers=response["headers"])
        stream = web.StreamResponse(headers=response["headers"])
        await stream.prepare(request)
        for chunk in response["chunks"]:
            await stream.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await stream.write(b"data: [DONE]\n\n")
        return stream

    model.router.add_post("/v1/chat/completions", respond)
    bridge = web.Application()

    async def read(request):
        return web.json_response({
            "/v1/self": {"primary": "generated-self", "aliases": []},
            "/v1/groups": [{"id": "g1", "members": [["+852111"]]}],
            "/v1/health": {"ok": True},
        }[request.path])

    for path in ("/v1/self", "/v1/groups", "/v1/health"):
        bridge.router.add_get(path, read)
    apps = {"llm.base_url": model, "honcho.base_url": _empty_honcho_app(),
            "platforms.signal.base_url": bridge}
    apps.update({f"providers.{name}.base_url": model for name in cfg.providers})
    return apps


async def seed(url):
    engine = create_sql_engine(url)
    try:
        message = NormalizedMessage("signal", "g1", "group", "+852111", "Miles",
                                    NOW - 1000, "Generated protocol input.", {})
        mid = await SqlRecordStore(engine).append(message=message, person_id="miles_novak")
        sid = await SqlStimulusStore(engine).append(stimulus=NewStimulus(
            unit="generated-unit", kind="message", item="message", ref=f"messages:{mid}",
            at_ms=NOW - 1000, platform="signal", conversation_id="g1",
            person_id="miles_novak"), recorded_at_ms=NOW - 1000)
        assert sid is not None
        return sid
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_real_recorded_wake_settles_and_both_judges_receive_terminal_evidence(
    clean_pg_url, evaluation_config, tmp_path, monkeypatch,
):
    from evals.backbone import _prefill_capture

    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated-token")
    _prefill_capture()
    policy = default_policy(("signal", "g1"), unit="generated-unit") + "home_room = true\n"
    identity = ('[persons.miles_novak]\nname="Miles"\n'
                '[[persons.miles_novak.signal]]\nphone="+852111"\n')
    policy_path, identity_path = tmp_path / "policy.toml", tmp_path / "identity.toml"
    policy_path.write_text(policy)
    identity_path.write_text(identity)
    overrides = {"platforms.signal.mode": "bridge", "platforms.signal.enabled": True,
                 "cognition.enabled": True}
    cfg = build_config(SubjectSpec(database_url=clean_pg_url, workdir=tmp_path / "declared",
                                  policy=policy, identity=identity, overrides=overrides))
    received = []
    apps = services(cfg, received)
    sid = await seed(clean_pg_url)
    args = SimpleNamespace(workdir=tmp_path / "subject", policy=policy_path,
        identity=identity_path, overrides=overrides, database_url=clean_pg_url,
        judge_a=MODELS[0], judge_b=MODELS[1], stop_after_tokens=10000, inspect_only=False)
    case = Case("generated-runtime", "generated-unit", (11,), (
        Expectation(0, "must_not", "note", "No note is created by the terminal-only script."),))
    with eval_host_lock() as lock_fd:
        evidence = RunEvidence(private_root=tmp_path / "capture", run_id="generated-runtime",
                               metadata={"mode": "protocol-rehearsal"}, lock_fd=lock_fd)
        budget = {}
        with evidence.capture(trace.get_tracer_provider()), measured_run_budget(
            args, evidence, tmp_path, budget, max_requests=4, max_output_tokens=20000,
        ):
            async with IsolatedServices(
                database_url=clean_pg_url, applications=apps, redis=True,
            ) as environment:
                args.environment = environment
                result = await _run_wake(
                    args, case, 0, 11, NOW, clean_pg_url, "settled", Judges(cfg, MODELS, tmp_path),
                    evidence, tmp_path, ("signal", "g1"), recorded_meta={
                        "stimulus_ids": [sid], "self_ids": [], "had_session": True})
                assert result["status"] == "PASS", (
                    result.get("step"), result.get("error"), environment.denied)
                assert result["session_id"] is not None
                terminal = result["trajectory"]["final_response"]
                assert terminal["text"] == "Generated terminal response."
                engine = create_sql_engine(clean_pg_url)
                try:
                    async with engine.connect() as conn:
                        outcome = (await conn.execute(
                            sa.select(COGNITION_WAKE_OUTCOME))).mappings().one()
                    assert outcome["outcome"] == "closed"
                    assert outcome["session_id"] == result["session_id"]
                finally:
                    await engine.dispose()
                assert not environment.denied
    events = read_events(evidence.path)
    assert validate_events(events)["status"] == "PASS"
    requests = [json.loads(e["attributes"]["nora.model.request.body"]) for e in events
                if e.get("name") == "nora.model.request"]
    assert requests == received
    assert len(requests) == budget["run_budget"]["requests_used"] == 4
    assert requests[0]["tools"] and "Generated protocol input." in json.dumps(requests[0])
    assert [r["model"] for r in requests[-2:]] == MODELS
    for request in requests[-2:]:
        assert "Generated terminal response." in json.dumps(request)
    assert len(result["expectations"]) == 1
    assert len(result["expectations"][0]["answers"]) == 2
    assert not environment.active
