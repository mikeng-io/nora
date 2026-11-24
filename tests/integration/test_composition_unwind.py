"""`build_dependencies` owns what it acquires BEFORE `Dependencies` exists.

Every factory registers its resource with the `StartupOwner` the moment it
is built — before the next `await` that could strand it — and a failed or
cancelled build unwinds them in normal shutdown order. A successful build
transfers that ownership to `close_dependencies` — exactly once, never
twice. These tests observe REAL resources: aiohttp sessions against local
fake bridges, a real `flock` on the scoped computer's journal, a real Redis
client on the disposable container, the SQL engine's dispose, and the
reload task's cancellation.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import redis.asyncio as aioredis
from aiohttp import web
from aiohttp.test_utils import TestServer
from openai import AsyncOpenAI

import nora.app.composition as composition
import nora.app.composition_bridge as composition_bridge
import nora.computer.wire as computer_wire
from nora.agent import factory
from nora.app.computer_queue import ComputerQueueRunner, SegmentRegistry
from nora.app.lifecycle import StartupOwner, close_dependencies
from nora.config import load
from nora.config_reload import ReloadFiles
from nora.infra.computer.manager import ScopeBrowserManager
from nora.infra.sql.audit import SqlRecordStore
from nora.memory.domain.acts import Outcome
from nora.obs.metrics import Metrics
from nora.platform.bridge.client import BridgeAdapter
from nora.platform.registry import AdapterRegistry, AdapterRegistryError
from nora.tag import Debounce
from tests.builders import override_config
from tests.unit.test_tool_concurrency import _Gauge
from tests.unit.test_tool_handover import _Acts, _agent, _pass, _tool

ROOT = Path(__file__).resolve().parents[2]

TOKEN = "test-bearer"  # noqa: S105 — a synthetic fixture token, not a secret
PRIMARY = "+85290000100"


# ---------------------------------------------------------------- fixtures


@dataclass(frozen=True)
class _SignalCfg:
    enabled: bool = False
    base_url: str = "http://127.0.0.1:1"
    account: str = ""
    mode: str = "direct"


@dataclass(frozen=True)
class _DiscordCfg:
    enabled: bool = False
    token_env: str = "DISCORD_BOT_TOKEN"  # noqa: S105 — an env-var NAME
    base_url: str = "http://127.0.0.1:1"
    mode: str = "direct"


@dataclass(frozen=True)
class _WhatsAppCfg:
    enabled: bool = False
    base_url: str = "http://127.0.0.1:1"
    mode: str = "direct"


@dataclass(frozen=True)
class _InstagramCfg:
    enabled: bool = False
    base_url: str = "http://127.0.0.1:1"
    mode: str = "direct"


@dataclass(frozen=True)
class _PlatformsCfg:
    signal: _SignalCfg = field(default_factory=_SignalCfg)
    discord: _DiscordCfg = field(default_factory=_DiscordCfg)
    whatsapp: _WhatsAppCfg = field(default_factory=_WhatsAppCfg)
    instagram: _InstagramCfg = field(default_factory=_InstagramCfg)


def _bridge_app(state: dict[str, Any]) -> web.Application:
    """A fake bridge's `/v1/self` — see `_state` for the knobs."""

    @web.middleware
    async def auth(req: web.Request, handler):
        if req.headers.get("Authorization") != f"Bearer {TOKEN}":
            return web.json_response({"error": "unauthorized"}, status=401)
        return await handler(req)

    async def get_self(req: web.Request) -> web.Response:
        state["calls"] += 1
        if state["response_delay"]:
            await asyncio.sleep(state["response_delay"])
        if state["calls"] <= state["not_ready"]:
            return web.json_response(
                {"error": "identity_unavailable"}, status=state["status"])
        return web.json_response(
            {"platform": state["platform"], "primary": PRIMARY,
             "aliases": [PRIMARY], "handle": "nora"})

    app = web.Application(middlewares=[auth])
    app.router.add_get("/v1/self", get_self)
    return app


def _state(platform: str, *, not_ready: int = 0, status: int = 503,
           response_delay: float = 0.0) -> dict[str, Any]:
    """`status` while calls ≤ `not_ready`, else success; `response_delay` sleeps."""
    return {"calls": 0, "platform": platform, "not_ready": not_ready,
            "status": status, "response_delay": response_delay}


@asynccontextmanager
async def _serve(state: dict[str, Any]) -> AsyncIterator[str]:
    server = TestServer(_bridge_app(state))
    await server.start_server()
    try:
        yield str(server.make_url("")).rstrip("/")
    finally:
        await server.close()


@pytest.fixture
def track_bridge_sessions(monkeypatch):
    """Every aiohttp session a BridgeAdapter opens — the real resource an
    unwind must close. `sessions` is the positive control (a session exists
    to be closed); `closed` records each adapter `close()` actually ran."""
    sessions: list[Any] = []
    closed: list[Any] = []
    orig_client = BridgeAdapter._client
    orig_close = BridgeAdapter.close

    async def client(self):
        session = await orig_client(self)
        sessions.append(session)
        return session

    async def close(self):
        closed.append(self)
        await orig_close(self)

    monkeypatch.setattr(BridgeAdapter, "_client", client)
    monkeypatch.setattr(BridgeAdapter, "close", close)
    return SimpleNamespace(sessions=sessions, closed=closed)


@pytest.fixture
def unwind_spies(monkeypatch):
    """One spy per acquisition owner the unwind must reach — each calls the
    REAL close through, so "called" means "actually closed"."""
    seen = SimpleNamespace(
        audit=[], created_clients=[], closed_clients=[],
        created_redis=[], closed_redis=[], reload_started=None,
        reload_cancelled=None,
    )

    orig_aclose = SqlRecordStore.aclose
    async def audit(self):
        seen.audit.append(self)
        await orig_aclose(self)
    monkeypatch.setattr(SqlRecordStore, "aclose", audit)

    orig_create = factory.create_client
    def create_client(*args, **kwargs):
        c = orig_create(*args, **kwargs)
        seen.created_clients.append(c)
        return c
    monkeypatch.setattr(factory, "create_client", create_client)

    orig_cclose = AsyncOpenAI.close
    async def client_close(self):
        seen.closed_clients.append(self)
        await orig_cclose(self)
    monkeypatch.setattr(AsyncOpenAI, "close", client_close)

    orig_from_url = aioredis.from_url
    def from_url(url, **kwargs):
        c = orig_from_url(url, **kwargs)
        seen.created_redis.append(c)
        return c
    monkeypatch.setattr(aioredis, "from_url", from_url)

    orig_raclose = aioredis.Redis.aclose
    async def redis_aclose(self, *args, **kwargs):
        seen.closed_redis.append(self)
        await orig_raclose(self, *args, **kwargs)
    monkeypatch.setattr(aioredis.Redis, "aclose", redis_aclose)

    orig_run = ReloadFiles.run
    def run(self):
        # `spawn(reload_files.run())` calls the method to MAKE the coroutine —
        # record it here so a cancellation before the task ever runs is still
        # observed as a real started-and-cancelled task.
        seen.reload_started = self
        coro = orig_run(self)

        async def watch() -> None:
            try:
                await coro
            except asyncio.CancelledError:
                seen.reload_cancelled = self
                raise

        return watch()
    monkeypatch.setattr(ReloadFiles, "run", run)

    return seen


@pytest.fixture
def cfg(tmp_path, monkeypatch, clean_pg_url, example_config_path):
    """The same wiring fixture `tests/test_composition.py` runs the real
    composition root against — direct-mode signal, throwaway database."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    dst = example_config_path
    (tmp_path / "identity.toml").write_text(
        '[persons.miles]\nname = "Miles"\n[[persons.miles.signal]]\nphone = "+852111"\n'
    )
    (tmp_path / "policy.toml").write_text(
        'default = "deny"\n[[conversations]]\nplatform = "signal"\n'
        'id = "g1"\nlabel = "g1"\npersons = "*"\n'
    )
    return override_config(load(dst), {
        "store.data_dir": tmp_path,
        "platforms.signal.mode": "direct",
        "platforms.signal.base_url": "http://127.0.0.1:18080",
        "platforms.signal.account": PRIMARY,
        "doctrine.dir": ROOT / "nora" / "doctrine",
        "dreamer.dir": ROOT / "nora" / "dreamer",
    })


@asynccontextmanager
async def _bridge_cfg(base: Any, state: dict[str, Any], monkeypatch):
    """`base` with signal in BRIDGE mode against a local fake bridge."""
    async with _serve(state) as base_url:
        monkeypatch.setenv("NORA_BRIDGE_TOKEN", TOKEN)
        yield override_config(base, {
            "platforms.signal.mode": "bridge",
            "platforms.signal.base_url": base_url,
        })


async def _build_or_unwind(coro: Any, owner: StartupOwner) -> Any:
    try:
        return await coro
    except BaseException:
        await owner.aclose()
        raise


# ------------------------------------------------- AdapterRegistry seams


async def test_a_later_bridge_failure_closes_the_adapters_already_built(
    track_bridge_sessions,
):
    """Signal's identity resolves against one fake; Discord's first answer
    is a hard failure. The registry raises — and the adapter it already
    built, PLUS the one in flight, close their real sessions."""
    owner = StartupOwner()
    signal_state = _state("signal")
    discord_state = _state("discord", not_ready=10**9, status=500)
    async with _serve(signal_state) as signal_url, _serve(discord_state) as discord_url:
        cfg = _PlatformsCfg(
            signal=_SignalCfg(enabled=True, mode="bridge", base_url=signal_url),
            discord=_DiscordCfg(enabled=True, mode="bridge", base_url=discord_url),
        )
        with pytest.raises(AdapterRegistryError):
            await _build_or_unwind(AdapterRegistry.from_config(
                cfg, metrics=Metrics(), bridge_token=TOKEN, owner=owner,
                sleep=_no_sleep, clock=lambda: 0.0), owner)

    assert signal_state["calls"] >= 1
    assert discord_state["calls"] == 1
    # Both adapters opened real sessions; every one is closed, and the
    # close call ran on the adapter objects themselves.
    assert len(track_bridge_sessions.sessions) == 2
    assert all(s.closed for s in track_bridge_sessions.sessions)
    assert {a.platform for a in track_bridge_sessions.closed} == {
        "signal", "discord"}


async def test_cancelling_identity_resolution_closes_the_in_flight_adapter(
    track_bridge_sessions,
):
    """A cancellation mid-`/v1/self` — the task is inside `resolve_identity`
    — still closes the adapter's opened session, and the cancellation is
    what propagates, not a substitute error."""
    owner = StartupOwner()
    state = _state("signal", response_delay=30.0)
    async with _serve(state) as base_url:
        cfg = _PlatformsCfg(
            signal=_SignalCfg(enabled=True, mode="bridge", base_url=base_url))
        task = asyncio.create_task(_build_or_unwind(
            AdapterRegistry.from_config(
                cfg, metrics=Metrics(), bridge_token=TOKEN, owner=owner),
            owner))
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert state["calls"] == 1
    assert len(track_bridge_sessions.sessions) == 1
    assert track_bridge_sessions.sessions[0].closed
    assert [a.platform for a in track_bridge_sessions.closed] == ["signal"]


async def _no_sleep(seconds: float) -> None:
    return None


# ----------------------------------------------------- StartupOwner unit


async def test_unwind_continues_through_a_close_that_fails():
    """One close raising must not strand the rest — AsyncExitStack unwinds
    every registered callback, then the unwind error propagates."""
    order: list[str] = []

    async def good(name: str) -> None:
        order.append(name)

    async def bad() -> None:
        order.append("bad")
        raise RuntimeError("close failed")

    owner = StartupOwner()
    owner.own_audit(SimpleNamespace(aclose=lambda: good("audit")))
    owner.own_adapter(bad)
    owner.own_background(SimpleNamespace(cancel_all=lambda: good("background")))

    with pytest.raises(RuntimeError, match="close failed"):
        await owner.aclose()

    # Unwind order is the shared close order: background, adapters, audit.
    assert order == ["background", "bad", "audit"]


async def test_replacing_a_registration_closes_only_the_latest():
    """`own_computer`/`own_bridge` REPLACE: the factory re-registers the
    wider close as it finishes more of itself, and the unwind runs only
    the last one."""
    closes: list[str] = []

    async def mark(name: str) -> None:
        closes.append(name)

    owner = StartupOwner()
    owner.own_computer(lambda: mark("partial"))
    owner.own_computer(lambda: mark("full"))
    await owner.aclose()
    assert closes == ["full"]


# --------------------------------------------------- build_dependencies


async def test_a_mid_build_failure_unwinds_everything_acquired(
    cfg, monkeypatch, unwind_spies, track_bridge_sessions,
):
    """A failure inside `tasks()`'s `hydrate` — after the bridge adapter,
    the honcho client and the engine all exist — unwinds every one of them,
    and the ORIGINAL error is what propagates."""
    state = _state("signal")
    async with _bridge_cfg(cfg, state, monkeypatch) as bridge:
        async def hydrate_fails(self):
            await asyncio.sleep(0)  # let the spawned reload task run once
            raise RuntimeError("hydrate broke")
        monkeypatch.setattr(Debounce, "hydrate", hydrate_fails)

        with pytest.raises(RuntimeError, match="hydrate broke"):
            await composition.build_dependencies(bridge)

    assert state["calls"] >= 1
    # The spawned reload task was running and was cancelled — not just
    # abandoned on the loop.
    assert unwind_spies.reload_started is not None
    assert unwind_spies.reload_cancelled is not None
    # The real resources closed: adapter session, engine.
    assert track_bridge_sessions.sessions
    assert all(s.closed for s in track_bridge_sessions.sessions)
    assert unwind_spies.audit


async def test_cancelling_the_build_unwinds_and_keeps_cancellation(
    cfg, monkeypatch, unwind_spies, track_bridge_sessions,
):
    """Cancelled inside `hydrate`, the build unwinds identically — adapter
    session, reload task, engine — and `CancelledError` is what escapes."""
    state = _state("signal")
    async with _bridge_cfg(cfg, state, monkeypatch) as bridge:
        reached = asyncio.Event()

        async def hydrate_hangs(self):
            reached.set()
            await asyncio.Event().wait()
        monkeypatch.setattr(Debounce, "hydrate", hydrate_hangs)

        task = asyncio.create_task(composition.build_dependencies(bridge))
        # Wait for the build to be INSIDE the await — otherwise the cancel
        # could land before `tasks()` even spawned the reload loop and the
        # test would be measuring nothing.
        await asyncio.wait_for(reached.wait(), timeout=60)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert state["calls"] >= 1
    assert unwind_spies.reload_cancelled is not None
    assert track_bridge_sessions.sessions
    assert all(s.closed for s in track_bridge_sessions.sessions)
    assert unwind_spies.audit


async def test_a_failure_inside_bridge_wiring_still_closes_its_redis(
    cfg, monkeypatch, unwind_spies, _redis_container,
):
    """The bridge Redis is owned the moment `from_url` returns: a raise while
    the runtime is still being assembled (before `BridgeRuntime` exists)
    unwinds the client too, not only failures after it."""
    redis_url = (
        f"redis://{_redis_container.get_container_host_ip()}:"
        f"{_redis_container.get_exposed_port(6379)}/0"
    )
    monkeypatch.setenv("NORA_REDIS_URL", redis_url)
    state = _state("signal")
    async with _bridge_cfg(cfg, state, monkeypatch) as bridge:
        def inspector_fails(*args, **kwargs):
            raise RuntimeError("inspector broke")
        monkeypatch.setattr(composition_bridge, "QueueInspector", inspector_fails)

        with pytest.raises(RuntimeError, match="inspector broke"):
            await composition.build_dependencies(bridge)

    assert len(unwind_spies.created_redis) == 1
    assert unwind_spies.created_redis[0] in unwind_spies.closed_redis


async def test_a_late_build_failure_unwinds_bridge_and_model_resources(
    cfg, monkeypatch, unwind_spies, track_bridge_sessions, _redis_container,
):
    """After the bridge runtime built its real Redis client and the model
    lane its real AsyncOpenAI, a failure still unwinds everything — and one
    broken close (`background.cancel_all`) stops nothing else."""
    redis_url = (
        f"redis://{_redis_container.get_container_host_ip()}:"
        f"{_redis_container.get_exposed_port(6379)}/0"
    )
    monkeypatch.setenv("NORA_REDIS_URL", redis_url)
    state = _state("signal")
    async with _bridge_cfg(cfg, state, monkeypatch) as bridge:
        def otp_fails(**kwargs):
            raise RuntimeError("otp broke")
        monkeypatch.setattr(composition, "build_otp", otp_fails)

        async def cancel_all_fails(self):
            raise RuntimeError("cancel_all broke")
        monkeypatch.setattr(
            composition.BackgroundTasks, "cancel_all", cancel_all_fails)

        with pytest.raises(RuntimeError, match="otp broke"):
            await composition.build_dependencies(bridge)

    # The bridge Redis was REALLY created (positive control), then closed —
    # the `aclose` call ran on that exact client object.
    assert len(unwind_spies.created_redis) == 1
    assert unwind_spies.created_redis[0] in unwind_spies.closed_redis

    # The model lane's client closed; the adapter session and engine are all
    # unwound PAST the failing `cancel_all`.
    assert unwind_spies.created_clients
    assert all(
        c in unwind_spies.closed_clients for c in unwind_spies.created_clients)
    assert track_bridge_sessions.sessions
    assert all(s.closed for s in track_bridge_sessions.sessions)
    assert unwind_spies.audit


async def test_a_successful_build_transfers_each_resource_once(
    cfg, monkeypatch, unwind_spies, track_bridge_sessions, _redis_container,
):
    """On success NOTHING registered unwinds early — every resource is open
    at return — and `close_dependencies` then closes each exactly once."""
    redis_url = (
        f"redis://{_redis_container.get_container_host_ip()}:"
        f"{_redis_container.get_exposed_port(6379)}/0"
    )
    monkeypatch.setenv("NORA_REDIS_URL", redis_url)
    state = _state("signal")
    async with _bridge_cfg(cfg, state, monkeypatch) as bridge:
        deps = await composition.build_dependencies(bridge)

    # Positive controls BEFORE the transfer: every resource is still open.
    assert state["calls"] >= 1
    assert track_bridge_sessions.sessions
    assert not any(s.closed for s in track_bridge_sessions.sessions)
    assert not unwind_spies.closed_clients
    assert not unwind_spies.closed_redis
    assert not unwind_spies.audit
    assert unwind_spies.created_redis
    assert await unwind_spies.created_redis[0].ping()

    await close_dependencies(deps)

    assert all(s.closed for s in track_bridge_sessions.sessions)
    assert all(
        c in unwind_spies.closed_clients for c in unwind_spies.created_clients)
    assert len(unwind_spies.closed_redis) == len(unwind_spies.created_redis)
    assert len(unwind_spies.audit) == 1
    assert unwind_spies.reload_cancelled is not None


async def test_shutdown_records_handovers_from_both_runner_factories_before_closing_sql(
    cfg, unwind_spies,
):
    deps = await composition.build_dependencies(
        override_config(cfg, {
            "engagement.tool_wait_seconds": 0.05, "participation.enabled": True,
        }),
    )

    class ObserveBeforeClose(_Acts):
        async def observe(self, act, outcome):
            assert not unwind_spies.audit
            await super().observe(act, outcome)

    assert deps.fire_path is not None
    runners = (deps.engagement._runner, deps.fire_path._proactive_runner)
    executors = [runner._tool_executor for runner in runners]
    gauges, stores = ([_Gauge(), _Gauge()], [ObserveBeforeClose(), ObserveBeforeClose()])
    try:
        for executor, gauge, store in zip(executors, gauges, stores, strict=True):
            await _pass(_agent("slow", _tool("slow", gauge, None), executor), store)
        assert all(gauge.running == 1 for gauge in gauges)
        assert all(store.reserved and not store.outcomes for store in stores)
    finally:
        await close_dependencies(deps)
        for gauge in gauges:
            gauge.gate.set()
        await asyncio.gather(
            *(task for executor in executors for task in executor.handed_over),
            return_exceptions=True,
        )

    assert unwind_spies.audit == [deps.audit]
    assert [store.outcomes for store in stores] == [
        [(store.reserved[0], Outcome("uncertain"))] for store in stores
    ]
    assert all(gauge.running == 0 for gauge in gauges)


async def test_a_scoped_computer_acquisition_unwinds_redis_and_journal(
    cfg, monkeypatch, unwind_spies, tmp_path, _redis_container,
):
    """The scoped runtime's Redis client and journal flock exist BEFORE the
    queue await — a later failure must release both, measured by a second
    `SegmentRegistry` taking the lock the leaked one would still hold."""
    redis_url = (
        f"redis://{_redis_container.get_container_host_ip()}:"
        f"{_redis_container.get_exposed_port(6379)}/0"
    )
    monkeypatch.setenv("NORA_COMPUTER_TOKEN", "test-token")
    monkeypatch.setenv("NORA_COMPUTER_REDIS_URL", redis_url)
    scoped_cfg = override_config(cfg, {
        "computer.enabled": True,
        "computer.base_url": "http://127.0.0.1:1",
        "computer.state_dir": str(tmp_path),
        "computer.scoped.enabled": True,
        "computer.scoped.registry": str(tmp_path / "scopes"),
    })

    async def adapters_fail(**kwargs):
        raise RuntimeError("adapters broke")
    monkeypatch.setattr(composition, "adapters", adapters_fail)

    with pytest.raises(RuntimeError, match="adapters broke"):
        await composition.build_dependencies(scoped_cfg)

    # The scoped Redis client was REALLY created, then closed.
    assert len(unwind_spies.created_redis) == 1
    assert unwind_spies.created_redis[0] in unwind_spies.closed_redis

    # The pending journal's flock was released — a second owner takes it
    # (while the leak lived, `_open_pending_store` would raise).
    second = SegmentRegistry(pending_dir=tmp_path / "computer")
    second.close()

    # The engine and everything before it unwound too.
    assert unwind_spies.audit


# ------------------------------------------------- the wrapper's promise


async def test_a_cleanup_cancellation_preserves_the_build_error(
    cfg, monkeypatch, unwind_spies, track_bridge_sessions,
):
    """A cancellation landing INSIDE the unwind — a close raising
    `CancelledError` — must not substitute for the build's own error: the
    RuntimeError propagates and the closes after the cancelled one ran."""
    state = _state("signal")
    async with _bridge_cfg(cfg, state, monkeypatch) as bridge:
        async def hydrate_fails(self):
            await asyncio.sleep(0)  # let the spawned reload task run once
            raise RuntimeError("hydrate broke")
        monkeypatch.setattr(Debounce, "hydrate", hydrate_fails)

        orig_adapter_close = BridgeAdapter.close

        async def adapter_close_cancels(self):
            track_bridge_sessions.closed.append(self)
            await orig_adapter_close(self)
            raise asyncio.CancelledError()
        monkeypatch.setattr(BridgeAdapter, "close", adapter_close_cancels)

        with pytest.raises(RuntimeError, match="hydrate broke"):
            await composition.build_dependencies(bridge)

    assert track_bridge_sessions.closed, "the cancelled close itself ran"
    # The close after it kept unwinding: the audit engine.
    assert track_bridge_sessions.sessions
    assert all(s.closed for s in track_bridge_sessions.sessions)
    assert unwind_spies.audit


async def test_a_close_failure_during_cancellation_keeps_the_cancellation(
    cfg, monkeypatch, unwind_spies, track_bridge_sessions,
):
    """Cancelled mid-build, an ordinary close failure must not replace the
    `CancelledError` — the cancellation is what escapes, the close failure
    is what gets logged."""
    state = _state("signal")
    async with _bridge_cfg(cfg, state, monkeypatch) as bridge:
        reached = asyncio.Event()

        async def hydrate_hangs(self):
            reached.set()
            await asyncio.Event().wait()
        monkeypatch.setattr(Debounce, "hydrate", hydrate_hangs)

        attempts: list[Any] = []
        async def audit_close_fails(self):
            attempts.append(self)
            raise RuntimeError("audit close broke")
        monkeypatch.setattr(SqlRecordStore, "aclose", audit_close_fails)

        task = asyncio.create_task(composition.build_dependencies(bridge))
        await asyncio.wait_for(reached.wait(), timeout=60)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert attempts, "the audit close ran and failed — the swallowed error"
    # Everything before it in the unwind still ran.
    assert track_bridge_sessions.sessions
    assert all(s.closed for s in track_bridge_sessions.sessions)


# ------------------------------------------- the scoped computer's nested unwind


async def test_a_scoped_computer_close_unwinds_past_a_failing_manager_close(
    cfg, monkeypatch, unwind_spies, tmp_path, _redis_container,
):
    """`browser_manager.aclose` failing inside the one outer close callback
    must not strand `runner.close` — the inner unwind keeps drain order and
    still releases Redis and the journal flock."""
    redis_url = (
        f"redis://{_redis_container.get_container_host_ip()}:"
        f"{_redis_container.get_exposed_port(6379)}/0"
    )
    monkeypatch.setenv("NORA_COMPUTER_TOKEN", "test-token")
    monkeypatch.setenv("NORA_COMPUTER_REDIS_URL", redis_url)
    scoped_cfg = override_config(cfg, {
        "computer.enabled": True,
        "computer.base_url": "http://127.0.0.1:1",
        "computer.state_dir": str(tmp_path),
        "computer.scoped.enabled": True,
        "computer.scoped.registry": str(tmp_path / "scopes"),
    })

    stops: list[int] = []
    orig_stop = ComputerQueueRunner.stop
    async def stop_spy(self, **kwargs):
        # Basis for draining at all: the queue loop only starts in
        # `ComputerLifecycle.start`, which runs post-build — no owned
        # worker can exist here, so the drain must find it empty.
        stops.append(len(self._executors._tasks))
        await orig_stop(self, **kwargs)
    monkeypatch.setattr(ComputerQueueRunner, "stop", stop_spy)

    manager_closes: list[Any] = []
    async def manager_close_fails(self):
        manager_closes.append(self)
        raise RuntimeError("manager close broke")
    monkeypatch.setattr(ScopeBrowserManager, "aclose", manager_close_fails)

    async def adapters_fail(**kwargs):
        raise RuntimeError("adapters broke")
    monkeypatch.setattr(composition, "adapters", adapters_fail)

    with pytest.raises(RuntimeError, match="adapters broke"):
        await composition.build_dependencies(scoped_cfg)

    assert stops == [0], "no executor had started — the drain was trivially empty"
    assert len(manager_closes) == 1
    assert len(unwind_spies.created_redis) == 1
    assert unwind_spies.created_redis[0] in unwind_spies.closed_redis
    second = SegmentRegistry(pending_dir=tmp_path / "computer")
    second.close()


async def test_a_scoped_computer_early_close_releases_the_journal_past_a_redis_failure(
    cfg, monkeypatch, tmp_path, _redis_container,
):
    """Before the queue wiring finishes the close owns only Redis + the
    journal flock: a `redis.aclose` failure must not keep the flock held."""
    redis_url = (
        f"redis://{_redis_container.get_container_host_ip()}:"
        f"{_redis_container.get_exposed_port(6379)}/0"
    )
    monkeypatch.setenv("NORA_COMPUTER_TOKEN", "test-token")
    monkeypatch.setenv("NORA_COMPUTER_REDIS_URL", redis_url)
    scoped_cfg = override_config(cfg, {
        "computer.enabled": True,
        "computer.base_url": "http://127.0.0.1:1",
        "computer.state_dir": str(tmp_path),
        "computer.scoped.enabled": True,
        "computer.scoped.registry": str(tmp_path / "scopes"),
    })

    attempts: list[Any] = []
    async def redis_close_fails(self, *args, **kwargs):
        attempts.append(self)
        raise RuntimeError("redis close broke")
    monkeypatch.setattr(aioredis.Redis, "aclose", redis_close_fails)

    async def queues_fail(**kwargs):
        raise RuntimeError("queues broke")
    monkeypatch.setattr(computer_wire, "_scoped_computer_queues", queues_fail)

    with pytest.raises(RuntimeError, match="queues broke"):
        await composition.build_dependencies(scoped_cfg)

    assert len(attempts) == 1, "the redis close ran and failed"
    # The flock was still released — a second owner takes it.
    second = SegmentRegistry(pending_dir=tmp_path / "computer")
    second.close()


# ----------------------------------------------------- adjacent acquisitions


async def test_a_late_build_failure_unwinds_direct_describer_clients(
    cfg, monkeypatch, unwind_spies, track_bridge_sessions, _redis_container,
):
    """A direct describer lane's `AsyncOpenAI` is folded into the model
    lane's client list — a late build failure closes it like the rest."""
    redis_url = (
        f"redis://{_redis_container.get_container_host_ip()}:"
        f"{_redis_container.get_exposed_port(6379)}/0"
    )
    monkeypatch.setenv("NORA_REDIS_URL", redis_url)
    monkeypatch.setenv("KIMI_CODE_API_KEY", "test-kimi-key")
    state = _state("signal")
    async with _bridge_cfg(cfg, state, monkeypatch) as bridge:
        describer_cfg = override_config(bridge, {
            "media.describe.video.enabled": True,
            "media.describe.video.provider": "kimi",
            "media.describe.video.model": "kimi-for-coding/k2p5",
        })

        def otp_fails(**kwargs):
            raise RuntimeError("otp broke")
        monkeypatch.setattr(composition, "build_otp", otp_fails)

        with pytest.raises(RuntimeError, match="otp broke"):
            await composition.build_dependencies(describer_cfg)

    kimi = [
        c for c in unwind_spies.created_clients
        if "kimi" in str(c.base_url)]
    assert len(kimi) == 1, "the direct lane built one real client"
    assert kimi[0] in unwind_spies.closed_clients
