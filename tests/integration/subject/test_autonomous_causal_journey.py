"""Generated production timers and control flow; model behavior remains unmeasured."""

import asyncio
import json
import socket
import time
from dataclasses import replace

import pytest
import sqlalchemy as sa
from aiohttp import web

from nora.__main__ import App
from nora.domain.ticks import TICK_SECONDS
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.schema import (
    BRIDGE_ACTION,
    COGNITION_SESSION,
    COGNITION_WAKE,
    COGNITION_WAKE_OUTCOME,
    MESSAGES,
    NOTE,
    NOTE_REVISION,
    REMINDERS,
)
from nora.platform.bridge.queue import StreamProducer
from nora.platform.bridge.service import action_http_status, action_response
from nora.subject.effects import effect_boundary, effect_scope, prepare_effect_reads, read_effects
from nora.subject.environment import IsolatedServices
from nora.subject.eval_evidence import read_events
from nora.subject.journey import envelope, settled_event
from nora.subject.journey_world import (
    action_service,
    finite_boundary,
    make_causal_protocol,
    memory_service,
)
from nora.subject.obtain import SubjectSpec, build_config, default_policy, obtain
from nora.testing.causal_protocol import CAUSAL_TIMING, CORRECTION, INITIAL, TITLE
from nora.testing.signal_daemon import FakeSignalDaemon
from tests.integration.subject.test_causal_runtime_journey import (
    runtime_evidence as runtime_evidence,
)
from tests.integration.subject.test_recorded_wake_runtime import services


async def rows(engine, table):
    async with engine.connect() as conn:
        return (await conn.execute(sa.select(table))).mappings().all()


async def wait_for_phase(protocol, phase, engine, task, seconds):
    async with asyncio.timeout(seconds):
        while protocol.phase < phase:
            assert not protocol.errors, protocol.errors
            if task.done():
                await task
                pytest.fail("production app stopped during the journey")
            outcomes = await rows(engine, COGNITION_WAKE_OUTCOME)
            assert all(row["outcome"] == "closed" for row in outcomes), outcomes
            await asyncio.sleep(0.25)


async def wait_for_delivery(engine, protocol, task):
    async with asyncio.timeout(15):
        while True:
            actions = await rows(engine, BRIDGE_ACTION)
            reminders = await rows(engine, REMINDERS)
            assert not protocol.errors and not task.done(), protocol.errors
            assert all(row["state"] != "failed" for row in reminders), reminders
            if (len(actions) == len(reminders) == 1 and actions[0]["state"] == "confirmed"
                    and reminders[0]["state"] == "done"):
                return
            await asyncio.sleep(0.05)


async def ingress(runtime, engine, task, text, index):
    reader = runtime.readers[0]
    timestamp = int(time.time() * 1000)
    original = envelope(reader, "g1", timestamp)
    incoming = replace(original, event_id=f"generated-journey-{index}",
        message={**original.message, "body": text, "raw": {"dataMessage": {"message": text}}})
    producer = StreamProducer(runtime.redis, reader.stream)
    _, canonical = await producer.enqueue(incoming)
    event, _ = await settled_event(engine, canonical, task)
    assert event["state"] == "done"
    return incoming


@pytest.mark.fresh_database
@pytest.mark.parametrize("runtime_evidence", [20], indirect=True)
async def test_real_app_autonomous_cognition_fire_and_later_correction(
    clean_pg_url, evaluation_config, tmp_path, monkeypatch, runtime_evidence,
):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated-token")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    policy = (default_policy(("signal", "g1"), unit="generated-unit")
              + "home_room = true\nproactive = true\n")
    overrides = {"platforms.signal.mode": "bridge", "platforms.signal.enabled": True,
                 "cognition.enabled": True, "heartbeat.enabled": True, "obs.metrics.port": port,
                 **CAUSAL_TIMING}
    spec = SubjectSpec(database_url=clean_pg_url, workdir=tmp_path / "subject",
                       policy=policy, overrides=overrides)
    cfg = build_config(spec)
    protocol = make_causal_protocol(cfg)
    feeds, requests, owners = [], [], {}
    apps = services(cfg, requests)
    apps["honcho.base_url"] = memory_service(feeds)
    model = finite_boundary(protocol)
    for key in tuple(apps):
        if key == "llm.base_url" or key.startswith("providers."):
            apps[key] = model
    daemon = FakeSignalDaemon(sent_at_ms=int(time.time() * 1000))

    async def action(request):
        body = await request.json()
        daemon.sent_at_ms = int(time.time() * 1000)
        result = await owners["actions"].run(body)
        return web.json_response(action_response(body["action_id"], result),
                                 status=action_http_status(result))

    apps["platforms.signal.base_url"].router.add_post("/v1/actions", action)
    apps["platforms.signal.base_url"].router.add_post("/api/v1/rpc", daemon.rpc)
    async with IsolatedServices(database_url=clean_pg_url, applications=apps, redis=True) as env:
        subject = await obtain(replace(spec, environment=env))
        app = App(cfg=subject.cfg, deps=subject.deps)
        runtime = subject.deps.bridge_runtime
        owners["actions"] = action_service(runtime, env)
        engine = create_sql_engine(clean_pg_url)
        await prepare_effect_reads(engine, env)
        scope, boundary = effect_scope(subject), await effect_boundary(engine)
        task = asyncio.create_task(app.run())
        try:
            async with asyncio.timeout(15):
                while not await runtime.redis.exists(runtime.readers[0].stream):
                    assert not task.done()
                    await asyncio.sleep(0.01)
            first = await ingress(runtime, engine, task, INITIAL, 1)
            await wait_for_phase(protocol, 4, engine, task,
                cfg.cognition.cue_quiet_seconds + cfg.cognition.tick_seconds + 30)
            assert len(await rows(engine, NOTE_REVISION)) == 1
            await wait_for_phase(protocol, 5, engine, task,
                cfg.reminders.min_lead_seconds + TICK_SECONDS + cfg.heartbeat.tick_seconds * 2 + 30)
            await wait_for_delivery(engine, protocol, task)
            await ingress(runtime, engine, task, CORRECTION, 2)
            await wait_for_phase(protocol, 9, engine, task,
                cfg.cognition.cue_quiet_seconds + cfg.cognition.tick_seconds + 30)
            await assert_journey(engine, protocol, first, daemon, feeds, scope, boundary,
                                 runtime_evidence[0])
            env.check()
        finally:
            task.cancel()
            try:
                with pytest.raises(asyncio.CancelledError):
                    await task
            finally:
                await app.shutdown()
                try:
                    effects = await read_effects(engine, since=boundary, unit=scope.unit,
                        rooms=scope.rooms, profile_keys=scope.profile_keys,
                        events=read_events(runtime_evidence[0].path))
                    runtime_evidence[0].retain("journey-shutdown-effects.json",
                                              json.dumps(effects, default=str).encode())
                finally:
                    await engine.dispose()
        assert not subject.deps.background._tasks
    assert not env.active
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))
    runtime_evidence[0].record("protocol_assertions", status="PASS", execution="PASS",
                             validity="PASS", behavior="UNMEASURED")


async def assert_journey(engine, protocol, first, daemon, feeds, scope, boundary, evidence):
    assert protocol.phase == 9 and len(protocol.requests) == 9 + protocol.background
    assert not protocol.errors
    async with asyncio.timeout(15):
        while True:
            if len(await rows(engine, COGNITION_WAKE_OUTCOME)) >= 2:
                break
            await asyncio.sleep(0.05)
    wakes = await rows(engine, COGNITION_WAKE)
    outcomes = await rows(engine, COGNITION_WAKE_OUTCOME)
    assert len(wakes) == len(outcomes) == 2
    assert all(row["reason"] == "strong_cue" and row["mode"] == "live"
               and row["tracked"] == 1 and not row["detail"].get("recorded_wake_forced")
               for row in wakes), wakes
    assert all(row["outcome"] == "closed" for row in outcomes), outcomes
    assert wakes[0]["at_ms"] - first.received_ms >= 120_000
    assert len(await rows(engine, COGNITION_SESSION)) == 2
    versions = await rows(engine, NOTE_REVISION)
    assert len(versions) == 2 and len({row["note_id"] for row in versions}) == 1
    assert "Cedar Hall" in str(versions) and len(daemon.sent) == 1
    actions = await rows(engine, BRIDGE_ACTION)
    assert len(actions) == 1 and actions[0]["state"] == "confirmed"
    sends = [row for row in await rows(engine, MESSAGES) if row["is_nora"]]
    assert len(sends) == 1 and sends[0]["delivery_status"] == "sent"
    assert sends[0]["id"] == actions[0]["message_id"]
    reminders = await rows(engine, REMINDERS)
    assert len(reminders) == 1 and reminders[0]["state"] == "done", reminders
    assert len(feeds) == 3
    assert [row["title"] for row in await rows(engine, NOTE)] == [TITLE]
    events = read_events(evidence.path)
    captured = [json.loads(e["attributes"]["nora.model.request.body"])
                for e in events if e.get("name") == "nora.model.request"]
    assert captured == protocol.requests
    assert not [e for e in events if e["kind"] == "log" and e.get("level") in {"ERROR", "CRITICAL"}
                and e.get("logger", "").startswith("nora.")]
    effects = await read_effects(engine, since=boundary, unit=scope.unit, rooms=scope.rooms,
                                profile_keys=scope.profile_keys, events=events)
    assert effects["status"] == "observed"
    assert {"note", "wake", "delivery"} <= {item["kind"] for item in effects["items"]}
    evidence.retain("autonomous-journey-effects.json", json.dumps(effects, default=str).encode())
