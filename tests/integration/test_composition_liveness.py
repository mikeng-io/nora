"""Composition under per-platform liveness
(design/platform-bridges/platform-liveness.md): a degraded platform never stops
the build, bridge misconfiguration does, and the composed runtime recovers a
degraded platform with no restart."""
# ruff: noqa: F811 — `cfg` is the shared composition fixture, imported below

from __future__ import annotations

import functools

import pytest

from nora.app.composition import build_dependencies
from nora.app.startup import StartupError
from tests.builders import override_config
from tests.test_composition import cfg, owned_dependencies  # noqa: F401


async def test_a_degraded_signal_still_composes_with_another_platform_live(cfg, monkeypatch):
    """Composition reads no identity unconditionally: Signal's resolution
    fails, Instagram's succeeds, and `build_dependencies` returns with Signal
    degraded, Instagram live and her Signal account unknown (`None`, never a
    placeholder)."""
    from nora.domain.ports import PlatformIdentity
    from nora.platform.instagram.adapter import InstagramAdapter
    from nora.platform.signal.adapter import SignalAdapter

    async def signal_down(self):
        raise RuntimeError("signal-cli unreachable")

    async def instagram_up(self):
        return PlatformIdentity(platform="instagram", primary="18247361241432477")

    monkeypatch.setattr(SignalAdapter, "resolve_identity", signal_down)
    monkeypatch.setattr(InstagramAdapter, "resolve_identity", instagram_up)

    deps = await build_dependencies(override_config(cfg, {
        "platforms.instagram.enabled": True,
        "platforms.instagram.base_url": "http://127.0.0.1:1",
    }))

    assert deps.registry.state_of("signal") == "degraded"
    assert deps.registry.state_of("instagram") == "live"
    assert deps.account is None
    assert "18247361241432477" in deps.registry.self_aliases()


async def _self_counter(aiohttp_server):
    from aiohttp import web

    calls: list[str] = []

    async def any_request(request):
        calls.append(request.path)
        return web.json_response({"primary": "+852999"})

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", any_request)
    server = await aiohttp_server(app)
    return str(server.make_url("")).rstrip("/"), calls


async def test_bridge_mode_without_a_shared_bearer_refuses_to_start(
        cfg, monkeypatch, aiohttp_server):
    """Composition owns this check, BEFORE the registry: no adapter is built
    and no bridge is asked who Nora is."""
    url, calls = await _self_counter(aiohttp_server)
    monkeypatch.delenv(cfg.bridges.token_env, raising=False)
    monkeypatch.setenv(cfg.bridges.redis_url_env, "redis://127.0.0.1:1/0")
    with pytest.raises(StartupError, match=r"bridges\.token_env"):
        await build_dependencies(override_config(cfg, {
            "platforms.signal.mode": "bridge", "platforms.signal.base_url": url}))
    assert calls == []


async def test_bridge_mode_without_a_redis_url_refuses_to_start(
        cfg, monkeypatch, aiohttp_server):
    url, calls = await _self_counter(aiohttp_server)
    monkeypatch.setenv(cfg.bridges.token_env, "t")
    monkeypatch.delenv(cfg.bridges.redis_url_env, raising=False)
    with pytest.raises(StartupError, match=r"bridges\.redis_url_env"):
        await build_dependencies(override_config(cfg, {
            "platforms.signal.mode": "bridge", "platforms.signal.base_url": url}))
    assert calls == []


async def test_all_platforms_degraded_composes_serves_503_and_recovers(
        cfg, monkeypatch, aiohttp_client, clean_pg_url):
    """Every enabled platform's identity fails: Nora still composes, the
    health app `App.run` serves answers 503 (nobody can reach her), and the
    recovery `App.run` spawns promotes Signal once it answers — the SAME
    health app then answers 200, with no rebuild and no restart."""
    import asyncio
    from types import SimpleNamespace

    from nora.__main__ import App
    from nora.domain.ports import MemoryHealth, PlatformIdentity
    from nora.platform.signal.adapter import SignalAdapter

    answering = {"up": False}
    identity = PlatformIdentity(platform="signal", primary="+85290000100")
    async def resolve(self):
        if not answering["up"]:
            raise RuntimeError("signal-cli unreachable")
        return identity

    monkeypatch.setattr(SignalAdapter, "resolve_identity", resolve)
    deps = await build_dependencies(cfg)
    assert deps.registry.state_of("signal") == "degraded"
    assert not deps.registry.live_platforms()

    async def reachable():
        return MemoryHealth(reachable=True)

    monkeypatch.setattr(deps.memory, "health", reachable)
    # The lessons evidence through its OWN wiring, built while Signal is degraded.
    from sqlalchemy.ext.asyncio import create_async_engine

    from nora.app.composition_dream import _lessons_evidence

    engine = create_async_engine(clean_pg_url)
    lessons = _lessons_evidence(
        allowlist=None, cfg=override_config(cfg, {"lessons.enabled": True}),
        content_checks=True, doctrine_store=deps.doctrine_store,
        dreamer_instructions=deps.dreamer_instructions, engine=engine,
        metrics=deps.metrics, model_client_for=lambda name: deps.client,
        persons=deps.persons, registry=deps.registry,
        route_model=lambda name, model: model or "nora")
    assert "+85290000100" not in lessons._evidence._self_ids
    # The listener is not running in this test; report its socket up so the
    # only variable /health sees is liveness.
    deps.registry.get("signal")._listener = SimpleNamespace(
        connected=True, last_event_at=None)
    app = App(cfg=cfg, deps=deps)
    client = await aiohttp_client(app.health_app(started_at=0.0))
    assert (await client.get("/health")).status == 503

    async def tick(seconds):
        await asyncio.sleep(0)

    task = app.start_platform_recovery(sleep=tick)
    answering["up"] = True
    for _ in range(50):
        if deps.registry.is_live("signal"):
            break
        await asyncio.sleep(0)
    response = await client.get("/health")
    body = await response.json()
    assert response.status == 200, body
    assert body["platforms"]["signal"]["state"] == "live"
    assert deps.registry.identity_for("signal") == identity
    # The COMPOSED long-lived alias holders were built while Signal was
    # degraded; each now sees her promoted id (a startup copy would not).
    holders = [*_reachable(deps, ("MentionPolicy", "ReplyToNoraPolicy")), lessons._evidence]
    assert {type(h).__name__ for h in holders} >= {
        "MentionPolicy", "ReplyToNoraPolicy", "LessonEvidence"}
    assert all("+85290000100" in h._self_ids for h in holders)
    await task
    await engine.dispose()


def _reachable(root, names, limit=500000):
    """Objects of the named classes reachable from `root`: the wired graph."""
    seen, found, queue = set(), [], [root]
    while queue and len(seen) < limit:
        obj = queue.pop()
        if id(obj) in seen or isinstance(obj, (str, bytes, int, float, type)):
            continue
        seen.add(id(obj))
        if type(obj).__name__ in names:
            found.append(obj)
        if isinstance(obj, dict):
            queue.extend(obj.values())
        elif isinstance(obj, (list, tuple, set, frozenset)):
            queue.extend(obj)
        else:
            queue.extend(getattr(obj, "__dict__", {}).values())
            queue.extend(getattr(obj, attr, None) for attr in getattr(obj, "__slots__", ()))
            # Closures, partials and bound methods hold wired objects too.
            queue.extend(c.cell_contents for c in getattr(obj, "__closure__", None) or ()
                         if c.cell_contents is not None)
            queue.append(getattr(obj, "__self__", None))
            if isinstance(obj, functools.partial):
                queue.extend((obj.func, *obj.args, *obj.keywords.values()))
    return found
