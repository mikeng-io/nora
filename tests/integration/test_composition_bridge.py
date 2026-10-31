"""The REAL composition path under `mode = "bridge"` (design/platform-bridges).

`build_dependencies` is the one place config becomes collaborators — these
tests drive it (and `App.run`'s own spawns/routes) rather than constructing
the runtime classes by hand, so the proof covers the swaps the deployment
actually boots: queue-only adapters, no app-side SDK client/music
manager/voice hub, a `BridgeRuntime` with its inspector wired to the same
Redis/Postgres/metrics the runner uses, and the authenticated queue
readout mounted on the health app.
"""

from __future__ import annotations

import asyncio
import contextlib
import tomllib
from pathlib import Path

import pytest
import tomli_w
from aiohttp import ClientSession, web

from nora.__main__ import build_app
from nora.app.bridge_queue import BridgeRuntime, stream_name
from nora.platform.bridge.client import BridgeAdapter
from nora.platform.bridge.inspection import QueueInspector
from nora.platform.discord.adapter import DiscordAdapter
from nora.platform.discord.bridge_control import BridgeDiscordAdapter
from nora.platform.signal.adapter import SignalAdapter

ROOT = Path(__file__).resolve().parents[2]

TOKEN = "it-bridge-token"  # noqa: S105 — a synthetic fixture token, not a secret


def _fake_bridge(routes: dict[str, dict]) -> web.Application:
    """The bridge's wire surface in miniature — enough of it for the
    registry's `resolve_identity` and a runtime `health()` to cross a real
    socket."""
    app = web.Application()

    async def _self(request: web.Request) -> web.Response:
        return web.json_response(routes["self"])

    async def _health(request: web.Request) -> web.Response:
        return web.json_response(routes.get("health", {"ok": True}))

    app.router.add_get("/v1/self", _self)
    app.router.add_get("/v1/health", _health)
    return app


@pytest.fixture
async def env(tmp_path, monkeypatch, aiohttp_server, pg_url,
              _redis_container, example_config_path):
    """An all-bridge deployment: signal + discord both `mode = "bridge"`,
    real disposable Postgres and Redis behind the env vars composition
    reads, fake bridge HTTP behind each `base_url`."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    redis_url = (
        f"redis://{_redis_container.get_container_host_ip()}:"
        f"{_redis_container.get_exposed_port(6379)}")
    monkeypatch.setenv("NORA_REDIS_URL", redis_url)
    monkeypatch.setenv("NORA_BRIDGE_TOKEN", TOKEN)

    signal_bridge = await aiohttp_server(_fake_bridge(
        {"self": {"primary": "+00000000100", "aliases": []}}))
    discord_bridge = await aiohttp_server(_fake_bridge(
        {"self": {"primary": "999999999", "aliases": []}}))

    with example_config_path.open("rb") as f:
        cfg = tomllib.load(f)
    cfg["platforms"]["signal"]["mode"] = "bridge"
    cfg["platforms"]["signal"]["base_url"] = str(
        signal_bridge.make_url("")).rstrip("/")
    cfg["platforms"]["signal"]["account_key"] = "sig-1"
    cfg["platforms"]["discord"]["enabled"] = True
    cfg["platforms"]["discord"]["mode"] = "bridge"
    cfg["platforms"]["discord"]["base_url"] = str(
        discord_bridge.make_url("")).rstrip("/")
    cfg["platforms"]["discord"]["account_key"] = "dc-1"
    cfg["honcho"]["base_url"] = str(signal_bridge.make_url("")).rstrip("/")
    cfg["store"]["data_dir"] = str(tmp_path)
    cfg["doctrine"]["dir"] = str(ROOT / "nora" / "doctrine")
    cfg["dreamer"]["dir"] = str(ROOT / "nora" / "dreamer")
    cfg["identity_file"] = str(tmp_path / "identity.toml")
    cfg["policy_file"] = str(tmp_path / "policy.toml")
    cfg["obs"]["metrics"]["port"] = 0
    (tmp_path / "identity.toml").write_text(
        "[persons.miles]\nname = \"Miles\"\n[[persons.miles.signal]]\nphone = \"+852111\"\n"
    )
    (tmp_path / "policy.toml").write_text(
        'default = "deny"\n[[conversations]]\nplatform = "signal"\n'
        'id = "Z3JvdXA="\nlabel = "group"\npersons = "*"\n'
        '[[conversations]]\nplatform = "discord"\n'
        'id = "dchan"\nlabel = "dchan"\npersons = "*"\n'
    )
    path = tmp_path / "config.toml"
    with path.open("wb") as f:
        tomli_w.dump(cfg, f)
    return path


async def test_bridge_mode_builds_the_runtime_and_only_proxies(env):
    app = await build_app(env)
    try:
        deps = app.deps
        # The runtime is real and complete: elected readers named by the
        # actual account keys, the inspector on the SAME redis+engine+metrics,
        # the metrics primed as real series.
        rt = deps.bridge_runtime
        assert isinstance(rt, BridgeRuntime)
        assert isinstance(rt.inspector, QueueInspector)
        assert {r.stream for r in rt.readers} == {
            stream_name("signal", "sig-1"), stream_name("discord", "dc-1")}
        assert all(r._metrics is deps.metrics for r in rt.readers)
        # `get` answers 0 only because prime CREATED the series — an
        # unprimed counter is absent entirely.
        assert deps.metrics.get(
            "nora_bridge_queue_events_total",
            platform="signal", event="claimed") == 0
        assert any(
            name == "nora_bridge_queue_events_total"
            and ("event", "claimed") in labels
            for (name, labels) in deps.metrics.snapshot())
        # Queue-only adapters — no SDK listener, no second voice/music owner.
        assert isinstance(deps.registry.adapters["signal"], BridgeAdapter)
        assert not isinstance(deps.registry.adapters["signal"], SignalAdapter)
        assert isinstance(
            deps.registry.adapters["discord"], BridgeDiscordAdapter)
        assert not isinstance(
            deps.registry.adapters["discord"], DiscordAdapter)
        assert deps.voice_hub is None
        assert deps.voice_event_consumer is None
        # The reader-facing health read crosses the real redis socket.
        assert (await rt.health())["ok"] is True
    finally:
        await app.shutdown()


async def test_app_run_spawns_inspection_and_mounts_the_readout(env,
                                                                monkeypatch):
    """`App.run` itself: the inspector loop is spawned beside `run()`, the
    queue routes serve on the authenticated health app, and shutdown stops
    the runtime and closes its Redis client."""
    app = await build_app(env)
    closed: list[bool] = []
    orig_aclose = app.deps.bridge_runtime.redis.aclose

    async def _record_aclose():
        closed.append(True)
        await orig_aclose()

    monkeypatch.setattr(app.deps.bridge_runtime.redis, "aclose",
                        _record_aclose)
    try:
        run = asyncio.ensure_future(app.run())
        port = None
        for _ in range(100):
            await asyncio.sleep(0.05)
            if app._health_runner is not None:
                addr = app._health_runner.addresses
                if addr:
                    port = addr[0][1]
                    break
        assert port is not None
        # The spawned inspector task lands in the shared background set —
        # polled, since the health port binds before run() reaches it.
        inspector_up = False
        for _ in range(100):
            if any("inspect_forever" in getattr(
                    t.get_coro(), "__qualname__", "")
                    for t in app.deps.background._tasks):
                inspector_up = True
                break
            await asyncio.sleep(0.05)
        assert inspector_up, "inspect_forever was never spawned"
        async with ClientSession() as s:
            no_auth = await s.get(
                f"http://127.0.0.1:{port}/app/v1/bridge/queue")
            assert no_auth.status == 401
            auth = await s.get(
                f"http://127.0.0.1:{port}/app/v1/bridge/queue",
                headers={"Authorization": f"Bearer {TOKEN}"})
            assert auth.status == 200
            body = await auth.json()
            assert stream_name("signal", "sig-1") in body
            assert stream_name("discord", "dc-1") in body
            for snap in body.values():
                # Every stream measured — a bounded real read, status in
                # the closed inspection vocabulary.
                assert snap["status"] in ("ok", "nothing_found",
                                          "unavailable")
            health = await s.get(
                f"http://127.0.0.1:{port}/app/v1/bridge/health",
                headers={"Authorization": f"Bearer {TOKEN}"})
            assert health.status == 200
            assert (await health.json())["ok"] is True
    finally:
        run.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await run
        await app.shutdown()
    # The shutdown path actually ran: the runtime was stopped (readers +
    # lanes + inspector share `_stop`) and the Redis client was closed.
    assert app.deps.bridge_runtime._stop.is_set()
    assert closed == [True]
