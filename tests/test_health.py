import pytest

from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.obs.health import LLMState, build_health_app
from nora.obs.metrics import Metrics
from nora.testing import FakeMemoryStore, FakeRecordStore
from tests.fakes import SinkSender


class _BrokenStore(FakeRecordStore):
    async def stats(self):
        raise RuntimeError("database gone")


@pytest.fixture
async def store(pg_url):
    run_migrations(pg_url)
    s = SqlRecordStore(create_sql_engine(pg_url))
    yield s
    await s.aclose()


@pytest.fixture
async def client(aiohttp_client, store):
    from nora.doctrine.loader import DoctrineStore

    metrics = Metrics()
    app = build_health_app(
        adapter=SinkSender(), store=store, honcho=FakeMemoryStore(),
        doctrine_store=DoctrineStore(
            __import__("pathlib").Path("nora/doctrine"), canary="C", metrics=metrics),
        metrics=metrics, started_at=0.0, llm_state=LLMState(),
    )
    c = await aiohttp_client(app)
    c.metrics = metrics
    c.honcho = app["honcho"]
    return c


async def test_health_reports_all_four_dependencies(client):
    body = await (await client.get("/health")).json()
    assert set(body) >= {"sse", "store", "honcho", "omniroute", "uptime_seconds",
                         "errors_since_restart", "doctrine_sha256", "strands_version"}


async def test_health_returns_200_when_everything_is_reachable(client):
    assert (await client.get("/health")).status == 200


async def test_health_sse_key_set_is_pinned(client):
    """`deploy/watchdog.sh` parses `body['sse']['last_event_age_seconds']`, and
    `/health` is additive-only — this is the only test enforcing that `sse` never
    loses a key a consumer already depends on."""
    body = await (await client.get("/health")).json()
    assert set(body["sse"]) >= {"connected", "last_event_age_seconds", "account", "platform"}


async def test_health_returns_503_when_the_store_is_unreachable(aiohttp_client):
    from pathlib import Path

    from nora.doctrine.loader import DoctrineStore

    metrics = Metrics()
    app = build_health_app(adapter=SinkSender(), store=_BrokenStore(),
                           honcho=FakeMemoryStore(), metrics=metrics,
                           doctrine_store=DoctrineStore(Path("nora/doctrine"), canary="C",
                                                       metrics=metrics),
                           started_at=0.0, llm_state=LLMState())
    c = await aiohttp_client(app)
    assert (await c.get("/health")).status == 503


async def test_health_returns_503_when_honcho_is_down(client):
    client.honcho.fail = True
    assert (await client.get("/health")).status == 503


async def test_health_reports_the_doctrine_sha256(client):
    body = await (await client.get("/health")).json()
    assert len(body["doctrine_sha256"]) == 64


async def test_health_reports_the_strands_version(client):
    body = await (await client.get("/health")).json()
    assert body["strands_version"] == "1.50.2"


async def test_health_reports_the_last_omniroute_success(aiohttp_client, store):
    from pathlib import Path

    from nora.doctrine.loader import DoctrineStore

    state = LLMState()
    state.last_success_at = 1700.0
    metrics = Metrics()
    app = build_health_app(adapter=SinkSender(), store=store,
                           honcho=FakeMemoryStore(), metrics=metrics,
                           doctrine_store=DoctrineStore(Path("nora/doctrine"), canary="C",
                                                       metrics=metrics),
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
    client.metrics.inc("nora_tags_total", platform="signal")
    text = await (await client.get("/metrics")).text()
    assert 'nora_tags_total{platform="signal"} 1.0' in text


async def test_health_never_raises_on_a_broken_dependency(client, store, monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("down")

    monkeypatch.setattr(store, "stats", boom)
    assert (await client.get("/health")).status == 503


def test_config_staleness_flags_a_file_edited_after_start(tmp_path):
    """The inode-gotcha canary: a config file whose disk
    mtime is NEWER than the process start is STALE — the operator's edit is not
    the one this process loaded (a recreate is needed, not a restart)."""
    from nora.obs.health import config_staleness

    f = tmp_path / "identity.toml"
    f.write_text("x = 1\n")
    mtime = f.stat().st_mtime
    stale = config_staleness({"identity": f}, started_at=mtime - 10)   # started BEFORE the edit
    fresh = config_staleness({"identity": f}, started_at=mtime + 10)   # started AFTER
    assert stale["identity"]["stale"] is True
    assert fresh["identity"]["stale"] is False
    assert stale["identity"]["disk_mtime"] is not None


def test_config_staleness_missing_file_never_raises_and_is_not_stale():
    from nora.obs.health import config_staleness

    out = config_staleness({"identity": "/nonexistent/identity.toml"}, started_at=0.0)
    assert out["identity"] == {"disk_mtime": None, "stale": False}


class _DeafAdapter:
    """A platform that is up enough to answer, and not connected."""

    platform = "discord"

    async def health(self):
        from nora.domain.ports import AdapterHealth
        return AdapterHealth(platform=self.platform, connected=False,
                             last_event_age_seconds=None)

    async def resolve_identity(self):
        from nora.domain.ports import PlatformIdentity
        return PlatformIdentity(platform=self.platform, primary="555000111")


async def test_health_reports_every_enabled_platform(aiohttp_client, store):
    """`/health` must not report `deps.adapter` alone, or a second platform
    could be wedged — Gateway down, every message dropped — while `/health`
    answered 200 on Signal's state. Additive: `sse` is untouched
    (`deploy/watchdog.sh` parses it), `platforms` is the per-platform block."""
    from nora.doctrine.loader import DoctrineStore

    metrics = Metrics()
    signal = SinkSender()
    app = build_health_app(
        adapter=signal, adapters={"signal": signal, "discord": _DeafAdapter()},
        store=store, honcho=FakeMemoryStore(),
        doctrine_store=DoctrineStore(
            __import__("pathlib").Path("nora/doctrine"), canary="C", metrics=metrics),
        metrics=metrics, started_at=0.0, llm_state=LLMState(),
    )
    client = await aiohttp_client(app)
    response = await client.get("/health")
    body = await response.json()

    assert set(body["platforms"]) == {"signal", "discord"}
    assert body["platforms"]["discord"]["connected"] is False
    assert body["platforms"]["discord"]["account"] == "555000111"
    assert body["sse"]["platform"] == "signal", "the pinned sse block stays Signal's"
    assert response.status == 503, (
        "a platform Nora cannot hear on must make the process unhealthy — "
        "Signal being up is not the whole answer once a second platform exists"
    )


class _DegradedAdapter:
    """An enabled Instagram adapter whose session got checkpointed.

    NOT the same thing as `_DeafAdapter`: this one is not merely disconnected,
    it is structurally unable to act until a human clears the challenge."""

    platform = "instagram"

    async def health(self):
        from nora.domain.ports import AdapterHealth
        return AdapterHealth(
            platform=self.platform, connected=False, last_event_age_seconds=None,
            degraded=True, detail="instagram session is 'checkpointed'",
        )

    async def resolve_identity(self):
        raise RuntimeError("no identity: the session is checkpointed")


async def test_health_REPORTS_a_degraded_platform_and_stays_200(aiohttp_client, store):
    """Both halves of a checkpointed platform's visibility, in one place.

    REPORTS: without the field, a checkpointed Instagram is indistinguishable
    from a quiet room — Nora is silently absent from every Instagram room and
    `/health` says only "not connected", which is what a bridge restarting
    says too.

    STAYS 200: an Instagram challenge is a routine overnight event. Flipping
    `/health` to 503 for it would fail the container healthcheck and take
    Signal and Discord down with it — the exact outcome the degradation path
    exists to prevent, arriving one layer later.
    """
    from nora.doctrine.loader import DoctrineStore

    metrics = Metrics()
    signal = SinkSender()
    app = build_health_app(
        adapter=signal, adapters={"signal": signal, "instagram": _DegradedAdapter()},
        store=store, honcho=FakeMemoryStore(),
        doctrine_store=DoctrineStore(
            __import__("pathlib").Path("nora/doctrine"), canary="C", metrics=metrics),
        metrics=metrics, started_at=0.0, llm_state=LLMState(),
    )
    client = await aiohttp_client(app)
    response = await client.get("/health")
    body = await response.json()

    entry = body["platforms"]["instagram"]
    assert entry["degraded"] is True
    assert "checkpointed" in entry["detail"]
    assert entry["account"] is None, "a degraded adapter has no identity to report"
    assert response.status == 200


async def test_a_merely_DISCONNECTED_platform_is_still_unhealthy(aiohttp_client, store):
    """The positive control for the test above. A degraded platform being
    exempt from `healthy` must not quietly exempt a wedged one — that would
    turn the degraded exemption into a way to stop noticing outages."""
    from nora.doctrine.loader import DoctrineStore

    metrics = Metrics()
    signal = SinkSender()
    app = build_health_app(
        adapter=signal, adapters={"signal": signal, "discord": _DeafAdapter()},
        store=store, honcho=FakeMemoryStore(),
        doctrine_store=DoctrineStore(
            __import__("pathlib").Path("nora/doctrine"), canary="C", metrics=metrics),
        metrics=metrics, started_at=0.0, llm_state=LLMState(),
    )
    client = await aiohttp_client(app)
    response = await client.get("/health")

    assert (await response.json())["platforms"]["discord"]["degraded"] is False
    assert response.status == 503


async def test_health_platforms_defaults_to_the_single_adapter(client):
    """No `adapters=` (a single-adapter caller) still reports
    the one adapter, so the block is never empty on a Signal-only deploy."""
    body = await (await client.get("/health")).json()
    assert set(body["platforms"]) == {"signal"}


def _fake_health_app(**kw):
    """A health app with no database in it: FakeRecordStore covers `store`."""
    from pathlib import Path

    from nora.doctrine.loader import DoctrineStore

    metrics = Metrics()
    return build_health_app(
        adapter=SinkSender(), store=FakeRecordStore(), honcho=FakeMemoryStore(),
        doctrine_store=DoctrineStore(Path("nora/doctrine"), canary="C", metrics=metrics),
        metrics=metrics, started_at=0.0, llm_state=LLMState(), **kw,
    )


async def test_health_reports_the_memory_index_state(aiohttp_client):
    from nora.domain.memory_index import IndexState

    async def index_state():
        return IndexState(status="complete", queue=3)

    c = await aiohttp_client(_fake_health_app(index_state=index_state))
    resp = await c.get("/health")
    assert resp.status == 200
    assert (await resp.json())["index"] == {"status": "complete", "queue": 3}


async def test_an_index_read_failure_is_unavailable_and_not_unhealthy(aiohttp_client):
    """R-27: a failed read says `unavailable`, never a guessed status; and a
    rebuilding or unreadable index must not flap the container healthcheck."""
    async def index_state():
        raise RuntimeError("database gone")

    c = await aiohttp_client(_fake_health_app(index_state=index_state))
    resp = await c.get("/health")
    assert resp.status == 200
    assert (await resp.json())["index"] == {"status": "unavailable"}


async def test_health_omits_the_index_block_when_unwired(aiohttp_client):
    c = await aiohttp_client(_fake_health_app())
    assert "index" not in await (await c.get("/health")).json()
