"""`AdapterRegistry.from_config` waits a bounded time for a bridge that is
reachable but still starting — the measured deploy defect where a
`config.toml` reload recreates nora and its bridges in the same moment,
so the first `/v1/self` answers 503 until the platform login lands and a
boot fails that a Docker restart then succeeds on.

The fake is a real aiohttp server answering `/v1/self` the way the real
bridge does — 503 `{"error": "identity_unavailable"}` until its "login"
lands (`nora.platform.discord.bridge_app.get_self`), then the real body,
with the bearer auth the real bridge requires. Time is the injected
`sleep`/`clock` seam, so the 60 s bound is exercised without waiting in
real time.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.obs.metrics import Metrics
from nora.platform import registry as reg
from nora.platform.registry import AdapterRegistry, AdapterRegistryError

TOKEN = "test-bearer"  # noqa: S105 — a synthetic fixture token, not a secret
PRIMARY = "100000000000000001"


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


def _bridge_cfg(platform: str, base_url: str) -> _PlatformsCfg:
    kwargs = {"enabled": True, "mode": "bridge", "base_url": base_url}
    if platform == "signal":
        return _PlatformsCfg(signal=_SignalCfg(**kwargs))
    if platform == "discord":
        return _PlatformsCfg(discord=_DiscordCfg(**kwargs))
    if platform == "whatsapp":
        return _PlatformsCfg(whatsapp=_WhatsAppCfg(**kwargs))
    raise AssertionError(platform)


class _FakeTime:
    """A clock that only advances when the code under test sleeps — the
    bound is exercised at real-loop speed, and a wait that lost its bound
    raises instead of hanging the suite."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        if len(self.sleeps) > 500:
            raise RuntimeError("the wait never reached its bound")
        self.now += seconds


def _bridge_app(state: dict) -> web.Application:
    """A fake bridge's `/v1/self`: the still-starting answer
    (`{"error": "identity_unavailable"}` at `state["status"]`) for the
    first `not_ready` calls, then `state["body"]` — the real route's
    success shape. Wrong auth gets the real bridge's 401, so a client
    that skipped its bearer fails the way the real bridge fails it."""

    @web.middleware
    async def auth(req: web.Request, handler):
        if req.headers.get("Authorization") != f"Bearer {TOKEN}":
            return web.json_response({"error": "unauthorized"}, status=401)
        return await handler(req)

    async def get_self(req: web.Request) -> web.Response:
        state["calls"] += 1
        if state["calls"] <= state["not_ready"]:
            return web.json_response(
                {"error": "identity_unavailable"}, status=state["status"])
        return web.json_response(state["body"])

    app = web.Application(middlewares=[auth])
    app.router.add_get("/v1/self", get_self)
    return app


def _state(*, not_ready: int, status: int = 503) -> dict:
    return {
        "calls": 0, "not_ready": not_ready, "status": status,
        "body": {"platform": "discord", "primary": PRIMARY,
                 "aliases": [PRIMARY], "handle": "nora"},
    }


@asynccontextmanager
async def _serve(state: dict) -> AsyncIterator[str]:
    server = TestServer(_bridge_app(state))
    await server.start_server()
    try:
        yield str(server.make_url("")).rstrip("/")
    finally:
        await server.close()


@pytest.mark.parametrize("platform", ["signal", "discord", "whatsapp"])
async def test_a_still_starting_bridge_is_waited_for_until_its_identity_resolves(
    platform: str, caplog: pytest.LogCaptureFixture,
):
    """503 for the first K answers, then the real identity — registration
    succeeds instead of failing the boot on the first answer. One bound
    for every bridge-backed platform, no per-platform case."""
    state = _state(not_ready=3)
    async with _serve(state) as base_url:
        clock = _FakeTime()
        with caplog.at_level(logging.INFO, logger="nora.platform.registry"):
            registry = await AdapterRegistry.from_config(
                _bridge_cfg(platform, base_url), metrics=Metrics(),
                bridge_token=TOKEN, sleep=clock.sleep, clock=clock)

    assert registry.identity_for(platform).primary == PRIMARY
    assert state["calls"] == 4
    assert clock.sleeps == [reg._BRIDGE_IDENTITY_INTERVAL_SECONDS] * 3
    starting = [r for r in caplog.records if "not ready" in r.getMessage()]
    resolved = [r for r in caplog.records if "resolved" in r.getMessage()]
    assert len(starting) == 1 and len(resolved) == 1


async def test_a_bridge_that_stays_not_ready_fails_startup_at_the_bound(
    caplog: pytest.LogCaptureFixture,
):
    """The wait is BOUNDED — a bridge that never becomes ready fails with
    the same startup error as today, not a hang and not a success."""
    state = _state(not_ready=10**9)
    async with _serve(state) as base_url:
        clock = _FakeTime()
        with (
            caplog.at_level(logging.WARNING, logger="nora.platform.registry"),
            pytest.raises(
                AdapterRegistryError,
                match="could not resolve its own identity"),
        ):
            await AdapterRegistry.from_config(
                _bridge_cfg("discord", base_url), metrics=Metrics(),
                bridge_token=TOKEN, sleep=clock.sleep, clock=clock)

    waits = int(reg._BRIDGE_IDENTITY_WAIT_SECONDS
                // reg._BRIDGE_IDENTITY_INTERVAL_SECONDS)
    assert state["calls"] == waits + 1
    assert len(clock.sleeps) == waits


@pytest.mark.parametrize(
    "state",
    [
        _state(not_ready=10**9, status=500),
        _state(not_ready=0) | {"body": {"handle": "nora"}},
    ],
    ids=["non-503-status", "malformed-identity"],
)
async def test_a_bridge_failure_that_is_not_still_starting_fails_immediately(
    state: dict,
):
    """Fail-closed, exactly as before: another status or a malformed
    identity is a startup error on the FIRST answer — never waited on."""
    async with _serve(state) as base_url:
        clock = _FakeTime()
        with pytest.raises(
                AdapterRegistryError,
                match="could not resolve its own identity"):
            await AdapterRegistry.from_config(
                _bridge_cfg("discord", base_url), metrics=Metrics(),
                bridge_token=TOKEN, sleep=clock.sleep, clock=clock)

    assert state["calls"] == 1
    assert clock.sleeps == []


async def test_a_refused_connection_is_waited_out_to_the_same_bound():
    """Connection refused — the container exists but its HTTP surface has
    not bound yet — is the same still-starting signal: waited, then the
    same bounded startup failure."""
    clock = _FakeTime()
    cfg = _bridge_cfg("discord", "http://127.0.0.1:1")
    with pytest.raises(
            AdapterRegistryError,
            match="could not resolve its own identity"):
        await AdapterRegistry.from_config(
            cfg, metrics=Metrics(), bridge_token=TOKEN,
            sleep=clock.sleep, clock=clock)

    waits = int(reg._BRIDGE_IDENTITY_WAIT_SECONDS
                // reg._BRIDGE_IDENTITY_INTERVAL_SECONDS)
    # It waited — a refused connection is "starting", not "dead" — then it
    # stopped at the same bound.
    assert len(clock.sleeps) == waits
