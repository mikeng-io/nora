import pytest

from nora.obs.health import LLMState, build_health_app
from nora.obs.metrics import Metrics
from nora.store import db
from tests.fakes import FakeHoncho, SinkSender


@pytest.fixture
async def client(aiohttp_client, tmp_path):
    dbp = tmp_path / "messages.db"
    await db.init(dbp)
    from nora.persona.loader import PersonaStore

    metrics = Metrics()
    app = build_health_app(
        adapter=SinkSender(), db_path=dbp, honcho=FakeHoncho(),
        persona_store=PersonaStore(
            __import__("pathlib").Path("nora/persona"), canary="C"),
        metrics=metrics, started_at=0.0, llm_state=LLMState(),
    )
    c = await aiohttp_client(app)
    c.metrics = metrics
    c.honcho = app["honcho"]
    return c


async def test_health_reports_all_four_dependencies(client):
    body = await (await client.get("/health")).json()
    assert set(body) >= {"sse", "sqlite", "honcho", "omniroute", "uptime_seconds",
                         "errors_since_restart", "persona_sha256", "strands_version"}


async def test_health_returns_200_when_everything_is_reachable(client):
    assert (await client.get("/health")).status == 200


async def test_health_returns_503_when_sqlite_is_unreachable(aiohttp_client, tmp_path):
    from pathlib import Path

    from nora.persona.loader import PersonaStore

    app = build_health_app(adapter=SinkSender(), db_path=tmp_path / "nope" / "x.db",
                           honcho=FakeHoncho(), metrics=Metrics(),
                           persona_store=PersonaStore(Path("nora/persona"), canary="C"),
                           started_at=0.0, llm_state=LLMState())
    c = await aiohttp_client(app)
    assert (await c.get("/health")).status == 503


async def test_health_returns_503_when_honcho_is_down(client):
    client.honcho.fail = True
    assert (await client.get("/health")).status == 503


async def test_health_reports_the_persona_sha256(client):
    body = await (await client.get("/health")).json()
    assert len(body["persona_sha256"]) == 64


async def test_health_reports_the_strands_version(client):
    body = await (await client.get("/health")).json()
    assert body["strands_version"] == "1.50.2"


async def test_health_reports_the_last_omniroute_success(aiohttp_client, tmp_path):
    from pathlib import Path

    from nora.persona.loader import PersonaStore

    state = LLMState()
    state.last_success_at = 1700.0
    await db.init(tmp_path / "m.db")
    app = build_health_app(adapter=SinkSender(), db_path=tmp_path / "m.db",
                           honcho=FakeHoncho(), metrics=Metrics(),
                           persona_store=PersonaStore(Path("nora/persona"), canary="C"),
                           started_at=0.0, llm_state=state)
    c = await aiohttp_client(app)
    body = await (await c.get("/health")).json()
    assert body["omniroute"]["last_success_at"] == 1700.0


async def test_metrics_endpoint_serves_prometheus_text(client):
    resp = await client.get("/metrics")
    assert resp.status == 200
    assert "text/plain" in resp.headers["Content-Type"]
    assert "# TYPE nora_tags_total counter" in await resp.text()


async def test_metrics_endpoint_reflects_recorded_counters(client):
    client.metrics.inc("nora_tags_total")
    assert "nora_tags_total 1" in await (await client.get("/metrics")).text()


async def test_health_never_raises_on_a_broken_dependency(client, monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("down")

    monkeypatch.setattr(db, "stats", boom)
    assert (await client.get("/health")).status == 503
