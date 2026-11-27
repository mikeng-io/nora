"""Drive live graph wakes from real bridge ingress with generated model responses."""

import asyncio
import json
from dataclasses import replace

import pytest
import sqlalchemy as sa

from nora.infra.sql.cognitive_graph_schema import GRAPH_STIMULUS_CLAIM
from nora.infra.sql.schema import MESSAGES
from nora.subject.environment import IsolatedServices
from nora.subject.eval_evidence import read_events
from nora.subject.journey import JourneyDriver
from nora.subject.journey_world import finite_boundary, make_causal_protocol
from nora.subject.obtain import SubjectSpec, build_config, default_policy, obtain
from nora.testing.causal_protocol import CAUSAL_TIMING, CORRECTION, INITIAL
from tests.integration.subject.protocol_services import services
from tests.integration.subject.test_causal_runtime_journey import (
    runtime_evidence as runtime_evidence,
)


async def _closed_wake(driver, body):
    claim = GRAPH_STIMULUS_CLAIM.c
    stmt = sa.select(claim.pass_id, claim.state, claim.attempts).join_from(
        GRAPH_STIMULUS_CLAIM, MESSAGES,
        (claim.graph_id == MESSAGES.c.graph_id) & (claim.unit == MESSAGES.c.unit),
    ).where(claim.claimant == "wake", MESSAGES.c.body == body)
    async with asyncio.timeout(15):
        while True:
            driver._require_running()
            async with driver.engine.connect() as conn:
                row = (await conn.execute(stmt)).one_or_none()
            if row is not None:
                assert row.state in {"claimed", "closed"}, row
                if row.state == "closed":
                    assert row.attempts == 0, row
                    return row.pass_id
            await asyncio.sleep(0.05)


@pytest.mark.fresh_database
@pytest.mark.parametrize("runtime_evidence", [20], indirect=True)
async def test_real_app_settles_graph_wakes_for_initial_message_and_correction(
    clean_pg_url, evaluation_config, tmp_path, monkeypatch, runtime_evidence,
):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated-token")
    policy = (default_policy(("signal", "g1"), unit="generated-unit")
              .replace('persons = "*"', 'persons = ["miles_novak"]')
              + "home_room = true\n")
    overrides = {"platforms.signal.mode": "bridge", "platforms.signal.enabled": True,
                 "cognition.quiet_seconds": 0.5,
                 "obs.metrics.port": 0, **CAUSAL_TIMING}
    spec = SubjectSpec(database_url=clean_pg_url, workdir=tmp_path / "subject",
                       policy=policy, overrides=overrides)
    cfg = build_config(spec)
    protocol = make_causal_protocol(cfg)
    apps = services(cfg, [])
    model = finite_boundary(protocol)
    for key in apps:
        if key == "llm.base_url" or key.startswith("providers."):
            apps[key] = model

    async with IsolatedServices(database_url=clean_pg_url, applications=apps, redis=True) as env:
        subject = await obtain(replace(spec, environment=env))
        async with JourneyDriver(subject) as driver:
            await driver.ingress(INITIAL, event_id="generated-initial")
            first = await _closed_wake(driver, INITIAL)
            await driver.ingress(CORRECTION, event_id="generated-correction")
            second = await _closed_wake(driver, CORRECTION)
            assert first != second
            env.check()
        assert not subject.deps.background._tasks

    assert not env.active
    assert not protocol.errors
    evidence, _ = runtime_evidence
    events = read_events(evidence.path)
    calls = [event for event in events if event.get("name") == "nora.model.request"]
    assert [json.loads(call["attributes"]["nora.model.request.body"])
            for call in calls] == protocol.requests
    wake_spans = [
        event for event in events if event["kind"] == "span_finished" and event["name"] == "wake"
    ]
    for event in wake_spans:
        assert event["attributes"].get("nora.outcome") == "closed", event
    settled_traces = {
        event["trace_id"] for event in wake_spans
        if event["attributes"].get("nora.tag_id") in {first, second}
    }
    assert len(settled_traces) == 2
    assert settled_traces <= {call["trace_id"] for call in calls}
    faults = [event for event in events if event["kind"] == "log"
              and event.get("logger", "").startswith("nora.")
              and event.get("level") in {"ERROR", "CRITICAL"}]
    assert not faults, faults
    evidence.record("protocol_assertions", status="PASS", execution="PASS",
                    validity="PASS", behavior="UNMEASURED")
