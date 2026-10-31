"""Real HTTP scrapes read the registry that durable capture increments."""

import json
import os
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
import redis.asyncio as aioredis
from aiohttp.test_utils import TestClient, TestServer
from prometheus_client.parser import text_string_to_metric_families
from sqlalchemy.ext.asyncio import create_async_engine

from nora.infra.sql.bridge_store import SqlBridgeStore
from nora.obs.metrics import Metrics
from nora.platform.bridge.envelope import Envelope
from nora.platform.bridge.journal import Journal
from nora.platform.bridge.queue import StreamProducer
from nora.platform.bridge.service import JournalCaptureSink, JournalPublisher
from nora.platform.discord.bridge_app import DiscordBridge
from tests.integration.test_bridge_entry_smoke import _DOTENV
from tests.integration.test_signal_bridge_routes import wrapper

API_TOKEN = "generated-current-bearer"  # noqa: S105 — generated fixture
LEGACY_TOKEN = "generated-legacy-bearer"  # noqa: S105 — generated fixture


@asynccontextmanager
async def producer(platform, tmp_path, monkeypatch, *, metrics, api_override=API_TOKEN):
    env = {
        "NORA_BRIDGE_API_TOKEN": API_TOKEN,
        "NORA_BRIDGE_TOKEN": LEGACY_TOKEN,
        "NORA_BRIDGE_ACCOUNT_KEY": "generated-account",
        "NORA_BRIDGE_JOURNAL": str(tmp_path / "journal.jsonl"),
        "NORA_BRIDGE_CORRELATION_KEY": "11" * 32,
        "NORA_BRIDGE_ATTACH_DIR": str(tmp_path / "attachments"),
        "NORA_BRIDGE_PROJECTION_DIR": str(tmp_path / "projection"),
        "SIGNAL_ACCOUNT": "generated-signal-account",
        "NORA_REDIS_URL": "redis://127.0.0.1:9/0",
        "DATABASE_URL": "postgresql+asyncpg://127.0.0.1:9/generated",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    if api_override is None:
        monkeypatch.delenv("NORA_BRIDGE_API_TOKEN", raising=False)
    else:
        monkeypatch.setenv("NORA_BRIDGE_API_TOKEN", api_override)
    cfg = wrapper.BridgeConfig.from_env()
    redis = aioredis.from_url(cfg.redis_url)
    engine = create_async_engine(cfg.database_url)
    if platform == "signal":
        bridge = wrapper.Bridge(
            cfg, store=SqlBridgeStore(engine), redis_client=redis, metrics=metrics)
        sink = bridge._sink
    else:
        journal = Journal(cfg.journal_path)
        publisher = JournalPublisher(
            journal=journal, producer=StreamProducer(redis, cfg.stream), metrics=metrics)
        sink = JournalCaptureSink(journal=journal, publisher=publisher, metrics=metrics)
        bridge = DiscordBridge(
            api_token=cfg.token, account_key=cfg.account_key,
            client=SimpleNamespace(), capture=sink, publisher=publisher,
            journal=journal, metrics=metrics)
    client = TestClient(TestServer(bridge.routes()))
    await client.start_server()
    try:
        yield client, sink
    finally:
        await client.close()
        await engine.dispose()
        await redis.aclose()


def captured_count(text, platform):
    return sum(
        sample.value
        for family in text_string_to_metric_families(text)
        for sample in family.samples
        if sample.name == "nora_bridge_queue_events_total"
        and sample.labels == {"platform": platform, "event": "captured"}
    )


def rendered_scrape_bearer(tmp_path, api_override):
    root = Path(__file__).resolve().parents[2]
    values = {
        **_DOTENV, "NORA_BRIDGE_TOKEN": LEGACY_TOKEN,
        "SIGNOZ_CLICKHOUSE_PASSWORD": "11" * 32,
        "SIGNOZ_POSTGRES_PASSWORD": "22" * 32, "SIGNOZ_JOURNAL_GID": "0",
    }
    if api_override is not None:
        values["NORA_BRIDGE_API_TOKEN"] = api_override
    else:
        values.pop("NORA_BRIDGE_API_TOKEN", None)
    envfile = tmp_path / "generated.env"
    envfile.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    clean_env = {k: v for k, v in os.environ.items()
                 if not k.startswith(("NORA_", "SIGNOZ_"))}
    rendered = []
    for path in ("deploy/signoz/deployment/compose.yaml", "docker-compose.yml"):
        result = subprocess.run(  # noqa: S603 — fixed public Compose paths
            ["docker", "compose", "--env-file", str(envfile), "-f", str(root / path),
             "config", "--format", "json"],
            env=clean_env, capture_output=True, text=True, check=False)
        assert result.returncode == 0, "generated Compose render failed"
        rendered.append(json.loads(result.stdout)["services"])
    backend, application = rendered
    bearer = backend["ingester"]["environment"]["NORA_BRIDGE_API_TOKEN"]
    application_bearers = {
        application[platform + "-bridge"]["environment"]["NORA_BRIDGE_API_TOKEN"]
        for platform in ("signal", "discord", "whatsapp", "instagram")
    }
    return bearer, application_bearers


@pytest.mark.parametrize("platform", ["signal", "discord"])
@pytest.mark.parametrize("api_override", [None, "", API_TOKEN])
async def test_durable_capture_counter_is_scrapable_with_effective_bearer(
    platform, api_override, tmp_path, monkeypatch,
):
    bearer, application_bearers = rendered_scrape_bearer(tmp_path, api_override)
    wrong_bearer = LEGACY_TOKEN if api_override else API_TOKEN
    async with producer(platform, tmp_path, monkeypatch, metrics=Metrics(),
                        api_override=api_override) as (http, sink):
        for headers in ({}, {"Authorization": f"Bearer {wrong_bearer}"}):
            assert (await http.get("/metrics", headers=headers)).status == 401
        headers = {"Authorization": f"Bearer {bearer}"}
        before = await http.get("/metrics", headers=headers)
        assert before.status == 200
        assert captured_count(await before.text(), platform) == 0
        envelope = Envelope(
            platform=platform, account_key="generated-account", kind="message",
            source_key="generated-source", event_id="generated-event",
            conversation_id="generated-room", conversation_type="group",
            sender_id="generated-sender", sender_name=None, sent_ms=1,
            received_ms=1, queued_ms=1, time_quality="upstream", message={})
        assert await sink.capture(envelope)
        after = await http.get("/metrics", headers=headers)
        assert after.status == 200
        assert "version=0.0.4" in after.headers["Content-Type"]
        assert captured_count(await after.text(), platform) == 1
        assert bearer == (api_override or LEGACY_TOKEN)
        assert application_bearers == {bearer}


@pytest.mark.parametrize("platform", ["signal", "discord"])
async def test_missing_metric_registry_reports_unavailable(platform, tmp_path, monkeypatch):
    async with producer(platform, tmp_path, monkeypatch, metrics=None) as (http, _):
        response = await http.get(
            "/metrics", headers={"Authorization": f"Bearer {API_TOKEN}"})
        assert response.status == 503
