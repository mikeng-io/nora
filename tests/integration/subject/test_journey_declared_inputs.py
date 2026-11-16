"""A declared production Envelope crosses the real journey input adapter
and the real Redis/SQL admission boundary unsubstituted — its actor,
source event identity and sent/received/queued clock arrive as declared,
never replaced by the generated sender, a generated event id or the wall
clock. The controls run the REAL bridge runtime (`driver.task =
runtime.run()`), the same producer/reader/runner seam
`test_bridge_queue.py` owns; a foreign (platform, account) is
unavailable, never routed to the first reader by accident.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from aiohttp import web

from nora.domain.models import NormalizedMessage
from nora.infra.sql.schema import BRIDGE_EVENT
from nora.platform.bridge.envelope import Envelope, canonical_source_key
from nora.platform.bridge.rehydrate import message_to_dict
from nora.platform.bridge.service import action_http_status, action_response
from nora.subject.environment import IsolatedServices
from nora.subject.eval_evidence import EvidenceError
from nora.subject.journey import JourneyDriver, declared_envelope
from nora.subject.journey_world import action_service, memory_service
from nora.subject.obtain import SubjectSpec, build_config, default_policy, obtain
from nora.testing.signal_daemon import FakeSignalDaemon
from tests.integration.subject.test_recorded_wake_runtime import services

# The driver prepares eval indexes — DDL — so every case owns a database.
pytestmark = pytest.mark.fresh_database

ACTOR, HANDLE = "+85290000110", "Ada"
# A declared source clock far from any wall clock the drive could invent.
SENT, RECEIVED, QUEUED = 1_600_000_000_000, 1_600_000_000_050, 1_600_000_000_090


def _declaration(**overrides):
    """A declared envelope's wire fields, generated for this control."""
    account = overrides.pop("account_key", "default")
    platform = overrides.pop("platform", "signal")
    source = canonical_source_key(
        platform, account, "g1", "message", "upstream-42")
    message = NormalizedMessage(
        platform, "g1", "group", ACTOR, HANDLE, SENT,
        "the declared body",
        {"dataMessage": {"message": "the declared body"}},
        message_id="upstream-42")
    fields = Envelope(
        platform=platform, account_key=account, kind="message",
        source_key=source, event_id="v1:declared-42", conversation_id="g1",
        conversation_type="group", sender_id=ACTOR, sender_name=HANDLE,
        sent_ms=SENT, received_ms=RECEIVED, queued_ms=QUEUED,
        time_quality="upstream", message=message_to_dict(message),
        admission_rev=1).to_fields()
    fields.update(overrides)
    return fields


@contextlib.asynccontextmanager
async def _world(clean_pg_url: str, tmp_path: Path, name: str):
    """One real subject over case-owned services, action routes wired."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    policy = (default_policy(("signal", "g1"), unit="generated-unit")
              + "home_room = true\n")
    overrides = {
        "platforms.signal.mode": "bridge", "platforms.signal.enabled": True,
        "cognition.enabled": True, "obs.metrics.port": port,
    }
    spec = SubjectSpec(database_url=clean_pg_url, workdir=tmp_path / name,
                       policy=policy, overrides=overrides)
    cfg = build_config(spec)
    requests: list[Any] = []
    feeds: list[Any] = []
    apps = services(cfg, requests)
    apps["honcho.base_url"] = memory_service(feeds)
    daemon = FakeSignalDaemon(sent_at_ms=int(time.time() * 1000))
    owners: dict[str, Any] = {}

    async def action(request):
        body = await request.json()
        daemon.sent_at_ms = int(time.time() * 1000)
        result = await owners["actions"].run(body)
        return web.json_response(
            action_response(body["action_id"], result),
            status=action_http_status(result))

    apps["platforms.signal.base_url"].router.add_post("/v1/actions", action)
    apps["platforms.signal.base_url"].router.add_post(
        "/api/v1/rpc", daemon.rpc)
    async with IsolatedServices(database_url=clean_pg_url,
                                applications=apps, redis=True) as env:
        subject = await obtain(replace(spec, environment=env))
        owners["actions"] = action_service(
            subject.deps.bridge_runtime, env)
        yield subject


@contextlib.asynccontextmanager
async def _running(driver: JourneyDriver):
    """The real bridge runtime as the driver's app task; stopped on exit."""
    runtime = driver.runtime
    driver.task = asyncio.create_task(runtime.run())
    try:
        yield runtime
    finally:
        runtime.stop()
        driver.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await driver.task
        driver.task = None
        await driver.engine.dispose()


async def test_declared_input_survives_real_ingress_and_admission(
        clean_pg_url, evaluation_config, tmp_path, monkeypatch):
    """The declared envelope crosses StreamProducer and production
    admission byte-for-byte: the settled event's identity, actor, room
    and declared clock are the declaration's own — and a past sent_ms
    reaches the event row, proving no wall-clock substitution."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated-token")
    async with _world(clean_pg_url, tmp_path, "declared") as subject:
        driver = JourneyDriver(subject)
        async with _running(driver):
            declared = declared_envelope(_declaration())
            incoming, event, work = await driver.ingress_declared(declared)
        assert incoming is declared
        assert event["state"] == "done"
        assert event["id"] == "v1:declared-42"
        assert event["platform"] == "signal"
        assert event["account_key"] == "default"
        assert event["source_key"] == declared.source_key
        assert event["conversation_id"] == "g1"
        assert event["sender_id"] == ACTOR
        assert event["received_ms"] == RECEIVED
        assert event["queued_ms"] == QUEUED
        assert event["time_quality"] == "upstream"
        wire = (event["payload"] or {}).get("envelope") or {}
        assert wire.get("event_id") == "v1:declared-42"
        assert wire.get("sender_id") == ACTOR
        assert wire.get("sender_name") == HANDLE
        assert wire.get("sent_ms") == SENT
        message = wire.get("message") or {}
        assert message.get("sender_id") == ACTOR
        assert message.get("timestamp") == SENT
        assert message.get("body") == "the declared body"
        assert work and all(
            w["state"] in ("done", "intentional_skip") for w in work)


async def test_declared_input_with_a_foreign_reader_is_unavailable(
        clean_pg_url, evaluation_config, tmp_path, monkeypatch):
    """A declaration naming a (platform, account) the runtime does not
    read is unavailable — it never lands on the first reader's stream."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated-token")
    async with _world(clean_pg_url, tmp_path, "foreign") as subject:
        driver = JourneyDriver(subject)
        async with _running(driver) as runtime:
            for fields in (_declaration(account_key="other-actor"),
                           _declaration(platform="whatsapp",
                                        account_key="declared-acct")):
                declared = declared_envelope(fields)
                with pytest.raises(EvidenceError,
                                   match="no running bridge reader"):
                    await driver.ingress_declared(declared)
                for reader in runtime.readers:
                    assert await runtime.redis.xlen(reader.stream) == 0


async def test_generated_input_still_crosses_the_same_boundary(
        clean_pg_url, evaluation_config, tmp_path, monkeypatch):
    """Existing generated send steps are unchanged: the generated actor,
    generated event id and generated clock still settle on the same real
    producer/admission path."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated-token")
    async with _world(clean_pg_url, tmp_path, "generated") as subject:
        driver = JourneyDriver(subject)
        async with _running(driver):
            _incoming, event, work = await driver.ingress(
                "the generated body", event_id="generated-control")
        assert event["state"] == "done"
        assert event["id"] == "generated-control"
        assert event["sender_id"] == "+852111"
        assert (event["payload"] or {}).get("envelope", {}).get(
            "sender_name") == "Miles"
        assert work and all(
            w["state"] in ("done", "intentional_skip") for w in work)
        async with driver.engine.connect() as conn:
            rows = (await conn.execute(sa.select(BRIDGE_EVENT).where(
                BRIDGE_EVENT.c.id == "generated-control"))).mappings().all()
        assert len(rows) == 1
