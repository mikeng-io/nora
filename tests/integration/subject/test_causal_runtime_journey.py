"""Generated startup and teardown control for the isolated production app."""

import asyncio
import json
import logging
import socket
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from aiohttp import web
from jsonschema import validate
from opentelemetry import trace

from nora.__main__ import App
from nora.domain.models import NormalizedMessage
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.schema import BRIDGE_ACTION, BRIDGE_EVENT, BRIDGE_WORK, MESSAGES, STIMULUS
from nora.platform.bridge.envelope import Envelope, canonical_source_key
from nora.platform.bridge.queue import StreamProducer
from nora.platform.bridge.rehydrate import message_to_dict
from nora.platform.bridge.service import DurableActionService, action_http_status, action_response
from nora.platform.signal.react import SignalReactor
from nora.platform.signal.send import SignalSender
from nora.subject.environment import IsolatedServices
from nora.subject.eval_evidence import RunEvidence, read_events, validate_events
from nora.subject.obtain import SubjectSpec, build_config, default_policy, obtain
from nora.subject.sandbox import eval_host_lock
from nora.testing.signal_daemon import FakeSignalDaemon
from scripts.memory_graph_spike import measured_run_budget
from tests.integration import test_signal_bridge_routes as signal_routes
from tests.integration.subject.test_recorded_wake_runtime import completion, services


@pytest.fixture
def runtime_evidence(tmp_path):
    from evals.backbone import _prefill_capture

    _prefill_capture()
    report = {}
    with eval_host_lock() as lock_fd:
        evidence = RunEvidence(run_id="generated-app", metadata={
            "mode": "protocol-rehearsal", "behavior": "UNMEASURED"},
            lock_fd=lock_fd, private_root=tmp_path / "capture")
        with (evidence.capture(trace.get_tracer_provider()),
              measured_run_budget(SimpleNamespace(stop_after_tokens=20000), evidence,
                                  tmp_path, report, max_requests=12, max_output_tokens=400000),
              evidence.case("generated-app", input={}, metadata={"behavior": "UNMEASURED"})):
            yield evidence, report
            assert any(e["kind"] == "protocol_assertions" and e["status"] == "PASS"
                       for e in read_events(evidence.path))
    assert validate_events(read_events(evidence.path))["status"] == "PASS"


def memory_service(feeds):
    application = web.Application()

    async def create(request):
        body = await request.json()
        if not isinstance(body.get("id"), str) or not body["id"]:
            return web.json_response({"error": "missing identity"}, status=422)
        return web.json_response(body)

    async def configure(request):
        body = await request.json()
        if not body or any(not isinstance(value, dict) for value in body.values()):
            return web.json_response({"error": "missing peer configuration"}, status=422)
        return web.json_response(body)

    async def members(request):
        return web.json_response({"items": [], "total": 0})

    async def join(request):
        body = await request.json()
        if not body.get("session_ids"):
            return web.json_response({"error": "missing sessions"}, status=422)
        return web.Response(status=204)

    async def feed(request):
        body = await request.json()
        if not body.get("messages") or any(
            not row.get("content") or not row.get("peer_id") for row in body["messages"]
        ):
            return web.json_response({"error": "missing message or author"}, status=422)
        feeds.extend(body["messages"])
        return web.json_response({"messages": body["messages"]})

    async def card(request):
        if request.match_info["peer"] not in {"miles_novak", "nora"}:
            return web.json_response({"error": "unrecorded peer"}, status=422)
        return web.json_response({"peer_card": []})

    async def representation(request):
        body = await request.json()
        if request.match_info["peer"] != "nora" or body.get("target") != "miles_novak":
            return web.json_response({"error": "unrecorded peer model"}, status=422)
        return web.json_response({"representation": ""})

    application.router.add_post("/v3/workspaces", create)
    for resource in ("peers", "sessions", "scopes"):
        application.router.add_post(f"/v3/workspaces/{{workspace}}/{resource}", create)
    application.router.add_post("/v3/workspaces/{workspace}/sessions/{session}/peers", configure)
    application.router.add_post("/v3/workspaces/{workspace}/sessions/{session}/messages", feed)
    application.router.add_post("/v3/workspaces/{workspace}/scopes/{scope}/sessions/list", members)
    application.router.add_post("/v3/workspaces/{workspace}/scopes/{scope}/sessions", join)
    application.router.add_get("/v3/workspaces/{workspace}/peers/{peer}/card", card)
    application.router.add_post(
        "/v3/workspaces/{workspace}/peers/{peer}/representation", representation)
    return application


def envelope(reader, room, timestamp, *, tagged=False):
    body = "Generated bridge admission input."
    source = canonical_source_key("signal", reader._account_key, room, "message", str(timestamp))
    message = NormalizedMessage("signal", room, "group", "+852111", "Miles", timestamp, body,
                                {"dataMessage": {"message": body}}, message_id=str(timestamp),
                                mentions=frozenset({"generated-self"}) if tagged else frozenset())
    return Envelope(platform="signal", account_key=reader._account_key, kind="message",
        source_key=source, event_id="generated-admission", conversation_id=room,
        conversation_type="group", sender_id="+852111", sender_name="Miles", sent_ms=timestamp,
        received_ms=timestamp, queued_ms=timestamp, time_quality="upstream",
        message=message_to_dict(message), admission_rev=1)


def reply_service(cfg, requests, errors):
    application = web.Application()

    async def respond(request):
        body = await request.json()
        position = len(requests)
        requests.append(body)
        try:
            assert position < 2 and body["model"] == cfg.llm.model
            assert body["stream"] is True
            tools = {tool["function"]["name"]: tool["function"]
                     for tool in body.get("tools", [])}
            if position == 0:
                assert "Generated bridge admission input." in json.dumps(body["messages"])
                arguments = {"text": "Generated protocol acknowledgement."}
                validate(arguments, tools["send_reply"]["parameters"])
                result = completion(cfg.llm.model, "", stream=True)["response"]
                result["chunks"][0]["choices"][0]["delta"] = {"role": "assistant",
                    "tool_calls": [{"index": 0, "id": "generated-reply", "type": "function",
                        "function": {"name": "send_reply", "arguments": json.dumps(arguments)}}]}
                result["chunks"][1]["choices"][0]["finish_reason"] = "tool_calls"
            else:
                assert not tools
                assert "Generated protocol acknowledgement." in json.dumps(body["messages"])
                result = completion(cfg.llm.model, json.dumps({
                    "standing": "Generated protocol acknowledgement.", "working_set": []}),
                    stream=True)["response"]
        except (AssertionError, KeyError) as exc:
            errors.append((position, body.get("model"), type(exc).__name__))
            return web.json_response({"error": "unmatched generated model request"}, status=422)
        stream = web.StreamResponse(headers=result["headers"])
        await stream.prepare(request)
        for chunk in result["chunks"]:
            await stream.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await stream.write(b"data: [DONE]\n\n")
        return stream

    application.router.add_post("/v1/chat/completions", respond)
    return application


def action_service(runtime, environment):
    address = environment.urls["platforms.signal.base_url"]
    executor = signal_routes.wrapper._SignalExecutor(
        SignalSender(address), SignalReactor(address))
    return DurableActionService(store=runtime.store, platform="signal",
        account_key=runtime.readers[0]._account_key, execute=executor,
        receipt_intents=frozenset)


async def settled_event(engine, canonical, task):
    async with asyncio.timeout(15):
        while True:
            async with engine.connect() as conn:
                event = (await conn.execute(sa.select(BRIDGE_EVENT).where(
                    BRIDGE_EVENT.c.id == canonical))).mappings().one_or_none()
                work = (await conn.execute(sa.select(BRIDGE_WORK).where(
                    BRIDGE_WORK.c.event_id == canonical))).mappings().all()
            if (event and event["state"] in {"done", "intentional_skip"}
                    and work and all(w["state"] in {"done", "intentional_skip"}
                                     for w in work)):
                break
            assert not task.done(), (event, work)
            assert all(w["state"] not in {"failed_retained", "uncertain"}
                       for w in work), work
            await asyncio.sleep(0.02)
    assert work and all(w["state"] in {"done", "intentional_skip"} for w in work), work
    return event, work


@pytest.mark.asyncio
@pytest.mark.parametrize("room,tagged", [
    ("g1", False), ("unlisted-generated-room", False), ("g1", True),
])
async def test_real_app_bridge_admission_settles_before_cancellation(
    clean_pg_url, evaluation_config, tmp_path, monkeypatch, caplog, room, tagged, runtime_evidence,
):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated-token")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    policy = default_policy(("signal", "g1"), unit="generated-unit") + "home_room = true\n"
    overrides = {"platforms.signal.mode": "bridge", "platforms.signal.enabled": True,
                 "cognition.enabled": True, "obs.metrics.port": port}
    spec = SubjectSpec(database_url=clean_pg_url, workdir=tmp_path / "subject",
                       policy=policy, overrides=overrides)
    cfg = build_config(spec)
    requests, feeds, actions, errors, owners = [], [], [], [], {}
    daemon = FakeSignalDaemon(sent_at_ms=int(time.time() * 1000) + 10000)
    apps = services(cfg, requests)
    apps["honcho.base_url"] = memory_service(feeds)
    if tagged:
        model = reply_service(cfg, requests, errors)
        for key in tuple(apps):
            if key == "llm.base_url" or key.startswith("providers."):
                apps[key] = model

        async def action(request):
            body = await request.json()
            if body.get("op") not in {"send", "react"} or not body.get("owner"):
                return web.json_response({"error": "invalid action"}, status=422)
            actions.append(body)
            reply = await owners["actions"].run(body)
            return web.json_response(action_response(body["action_id"], reply),
                                     status=action_http_status(reply))

        apps["platforms.signal.base_url"].router.add_post("/v1/actions", action)
        apps["platforms.signal.base_url"].router.add_post("/api/v1/rpc", daemon.rpc)
    async with IsolatedServices(database_url=clean_pg_url,
                                applications=apps, redis=True) as env:
        subject = await obtain(replace(spec, environment=env))
        app = App(cfg=subject.cfg, deps=subject.deps)
        runtime = subject.deps.bridge_runtime
        owners["actions"] = action_service(runtime, env)
        reader = runtime.readers[0]
        engine = create_sql_engine(clean_pg_url)
        task = asyncio.create_task(app.run())
        try:
            async with asyncio.timeout(15):
                while not await runtime.redis.exists(reader.stream):
                    if task.done():
                        await task
                        pytest.fail("app stopped before bridge startup")
                    await asyncio.sleep(0.01)
            assert app._health_runner is not None
            assert not task.done()
            incoming = envelope(reader, room, int(time.time() * 1000), tagged=tagged)
            producer = StreamProducer(runtime.redis, reader.stream)
            stream_id, canonical = await producer.enqueue(incoming)
            assert canonical == incoming.event_id and stream_id
            event, work = await settled_event(engine, canonical, task)
            async with engine.connect() as conn:
                recorded = (await conn.execute(sa.select(MESSAGES).where(
                    MESSAGES.c.platform_message_id == str(incoming.sent_ms)))).mappings().all()
                stimuli = (await conn.execute(sa.select(STIMULUS).where(
                    STIMULUS.c.conversation_id == room))).mappings().all()
            if room == "g1":
                assert len(recorded) == len(stimuli) == 1
                assert len(feeds) == (2 if tagged else 1)
                assert recorded[0]["person_id"] == "miles_novak"
                assert event["state"] == "done"
                assert {w["kind"] for w in work} >= {"ingest", "stimulus", "memory_feed"}
                assert feeds[0]["content"] == incoming.message["body"]
                if tagged:
                    async with engine.connect() as conn:
                        sent = (await conn.execute(sa.select(MESSAGES).where(
                            MESSAGES.c.is_nora == 1))).mappings().all()
                        receipts = (await conn.execute(sa.select(BRIDGE_ACTION).where(
                            BRIDGE_ACTION.c.op == "send"))).mappings().all()
                    assert len(sent) == len(receipts) == 1
                    assert sent[0]["delivery_status"] == "sent"
                    assert receipts[0]["state"] == "confirmed"
                    assert receipts[0]["message_id"] == sent[0]["id"]
                    assert sent[0]["ts"] == receipts[0]["platform_ms"] == daemon.sent_at_ms
                    assert len(daemon.sent) == 1
                    assert daemon.sent[0]["params"]["message"] == sent[0]["body"]
            else:
                assert event["state"] == "intentional_skip"
                assert recorded == stimuli == feeds == []
            assert not env.denied, [(e["method"], e["path"]) for e in env.denied]
            env.check()
        finally:
            task.cancel()
            try:
                with pytest.raises(asyncio.CancelledError):
                    await task
            finally:
                await app.shutdown()
                await engine.dispose()
        assert not subject.deps.background._tasks
        assert not errors
        assert len(requests) == (2 if tagged else 0)
        evidence, _ = runtime_evidence
        captured = [json.loads(e["attributes"]["nora.model.request.body"])
                    for e in read_events(evidence.path) if e.get("name") == "nora.model.request"]
        assert captured == requests
        assert not [(r.name, r.getMessage()) for r in caplog.records
                    if r.name.startswith("nora.") and r.levelno >= logging.ERROR]
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))
    assert not env.active
    runtime_evidence[0].record("protocol_assertions", status="PASS")
