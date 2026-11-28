"""Per-platform liveness, through the real gates
(docs/design/platform/README.md).

A degraded platform hears nothing (no supervised listen, no stream call, no
work claim) and sends nothing (every adapter class, every derived facade)
until the registry promotes it — and promotion registers its identity BEFORE
any of those gates opens, which is what the self-tag check needs.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools

import sqlalchemy as sa
from aiohttp import web
from sqlalchemy.ext.asyncio import create_async_engine

from nora.__main__ import _listen_when_live
from nora.app import runtime
from nora.app.bridge_queue import DbSpeakingLocks, StreamReader, WorkRunner
from nora.domain.models import ConversationTarget
from nora.domain.ports import PlatformIdentity, ReactionResult, SendResult
from nora.infra.sql.bridge_store import SqlBridgeStore
from nora.infra.sql.schema import BRIDGE_WORK
from nora.obs.metrics import Metrics
from nora.platform.bridge.client import BridgeAdapter, BridgeEndpoint
from nora.platform.discord.adapter import DiscordAdapter
from nora.platform.discord.bridge_control import BridgeDiscordAdapter
from nora.platform.instagram.adapter import InstagramAdapter
from nora.platform.registry import (
    AdapterRegistry,
)
from nora.platform.signal.adapter import SignalAdapter
from nora.platform.whatsapp.adapter import WhatsAppAdapter
from nora.testing import FakePlatformAdapter

IDENTITY = PlatformIdentity(platform="telegram", primary="tg-nora")


def _target(platform: str, *, voice: bool = False) -> ConversationTarget:
    return ConversationTarget(platform=platform, conversation_id="g/1",
                              conversation_type="group", voice=voice)


async def _send_and_react(adapter, platform: str, *, voice: bool = False):
    sent = await adapter.send(target=_target(platform, voice=voice), text="hello there")
    reacted = await adapter.react(target=_target(platform), emoji="👀",
                                  to_author="a", to_timestamp=1, to_message_id="m1")
    return sent, reacted


class _Upstream:
    """Counts every call a direct adapter makes to its sender/reactor — the
    first collaborator past the adapter, i.e. «upstream»."""

    def __init__(self) -> None:
        self.calls = 0

    async def send(self, *args, **kwargs):
        self.calls += 1
        return SendResult(ok=True)

    async def react(self, *args, **kwargs):
        self.calls += 1
        return ReactionResult(ok=True)


class _Control:
    """`BridgeDiscordAdapter`'s control client: counts the voice-state read
    the voice branch makes before any synthesis."""

    def __init__(self) -> None:
        self.calls = 0

    async def voice_state(self, guild_id):
        self.calls += 1
        return None  # no live session: the voice branch falls back to text

    async def close(self) -> None:
        return None


class _Speaker:
    async def speak(self, *, text):
        raise AssertionError("never reached: no live voice session")


async def _bridge(aiohttp_server):
    """A fake bridge that confirms every action and counts them."""
    actions: list[str] = []

    async def action(request):
        actions.append((await request.json())["op"])
        return web.json_response({"state": "confirmed", "platform_ms": 1})

    async def health(request):
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_post("/v1/actions", action)
    app.router.add_get("/v1/health", health)
    server = await aiohttp_server(app)
    return str(server.make_url("")).rstrip("/"), actions


class _CountingRedis:
    """Every stream call the reader makes is counted by name; the first call
    runs `on_first` (the ordering probe) and then stops the reader."""

    def __init__(self, on_first=None) -> None:
        self.calls: list[str] = []
        self._on_first = on_first

    def __getattr__(self, name):
        async def call(*args, **kwargs):
            self.calls.append(name)
            if self._on_first is not None and len(self.calls) == 1:
                self._on_first()
            raise asyncio.CancelledError
        return call


class _ElectedStore:
    @contextlib.asynccontextmanager
    async def advisory_lock(self, namespace, key):
        yield True


async def _settle() -> None:
    for _ in range(10):
        await asyncio.sleep(0)


async def _ignore(message) -> None:
    return None


async def test_a_degraded_platform_hears_and_sends_nothing_until_promoted(
        aiohttp_server, clean_pg_url):
    # ---- outbound: every adapter class and every derived facade ----------
    url, actions = await _bridge(aiohttp_server)
    endpoint = BridgeEndpoint(url, "t")
    control = _Control()
    bridge_root = BridgeAdapter("whatsapp", endpoint)
    discord_bridge = BridgeDiscordAdapter(
        delegate=BridgeAdapter("discord", endpoint), control=control, speaker=_Speaker())
    direct = {
        "signal": SignalAdapter("http://127.0.0.1:1", "+852999", metrics=Metrics()),
        "discord": DiscordAdapter("tok"),
        "whatsapp": WhatsAppAdapter("http://127.0.0.1:1"),
        "instagram": InstagramAdapter("http://127.0.0.1:1"),
    }
    upstreams = {name: _Upstream() for name in direct}
    for name, adapter in direct.items():
        adapter._sender = upstreams[name]
        adapter._reactor = upstreams[name]

    for adapter in (bridge_root, discord_bridge, *direct.values()):
        adapter.degrade("identity unavailable")
    # Derived AFTER the root degraded — and still bound by its latch.
    otp_facade = bridge_root.bind_owner(None)

    for name, adapter in direct.items():
        sent, reacted = await _send_and_react(adapter, name)
        assert not sent.ok and not reacted.ok
        assert upstreams[name].calls == 0, f"{name} reached upstream while degraded"
        assert (await adapter.health()).degraded
    for adapter, platform, voice in ((bridge_root, "whatsapp", False),
                                     (otp_facade, "whatsapp", False),
                                     (discord_bridge, "discord", True)):
        sent, reacted = await _send_and_react(adapter, platform, voice=voice)
        assert not sent.ok and not reacted.ok
        health = await adapter.health()
        assert health.degraded and health.platform == platform
    assert actions == [] and control.calls == 0

    for adapter in (bridge_root, discord_bridge, *direct.values()):
        adapter.promote()
    for name, adapter in direct.items():
        sent, reacted = await _send_and_react(adapter, name)
        assert sent.ok and reacted.ok
        assert upstreams[name].calls == 2
    for adapter, platform, voice in ((bridge_root, "whatsapp", False),
                                     (otp_facade, "whatsapp", False),
                                     (discord_bridge, "discord", True)):
        sent, reacted = await _send_and_react(adapter, platform, voice=voice)
        assert sent.ok and reacted.ok
    assert actions == ["send", "react"] * 3
    assert control.calls == 1  # the voice branch ran, then fell back to text
    for adapter in (bridge_root, discord_bridge):
        await adapter.close()

    # ---- inbound: listener, stream reader, work claim -------------------
    fake = FakePlatformAdapter(identities=[RuntimeError("logged out"), IDENTITY])
    registry = AdapterRegistry(adapters={"telegram": fake})
    fake.degrade("logged out")
    metrics = Metrics()
    listen = asyncio.ensure_future(runtime.supervise_adapter(runtime.SupervisedAdapter(
        platform="telegram", listen=functools.partial(
            _listen_when_live, registry, fake, {"on_message": _ignore})), metrics))
    redis = _CountingRedis()
    reader = asyncio.ensure_future(StreamReader(
        platform="telegram", account_key="a", redis=redis, store=_ElectedStore(),
        wait_live=functools.partial(registry.wait_live, "telegram")).run())

    engine = create_async_engine(clean_pg_url)
    store = SqlBridgeStore(engine)
    async with engine.begin() as conn:
        await conn.execute(sa.insert(BRIDGE_WORK).values(
            kind="participation", ordinal=0, platform="telegram", account_key="a",
            conversation_id="g/1", payload={}))
    runner = WorkRunner(store=store, redis=None, ingest=None, engagement=None,
                        slots=None, db_locks=DbSpeakingLocks(store), adapters={},
                        live_platforms=registry.live_platforms)
    try:
        await _settle()
        assert fake.listen_calls == 0
        assert redis.calls == [], "no XAUTOCLAIM, no XREADGROUP while degraded"
        assert await runner._claim_next(frozenset({"participation"})) is None

        await registry.recover("telegram", sleep=_no_wait)
        await _settle()
        assert fake.listen_calls == 1
        assert redis.calls, "the reader made its first stream call once live"
        work = await runner._claim_next(frozenset({"participation"}))
        assert work is not None and work.platform == "telegram"
    finally:
        listen.cancel()
        reader.cancel()
        await asyncio.gather(listen, reader, return_exceptions=True)
        await engine.dispose()


async def _no_wait(seconds: float) -> None:
    return None


class _Addressed:
    """`LessonEvidence`'s store: records which ids it was asked to match."""

    def __init__(self) -> None:
        self.self_ids: frozenset[str] = frozenset()

    async def last_run_ms(self, *, adaptor, key):
        return None

    async def addressed_since(self, *, self_ids, **kwargs):
        self.self_ids = frozenset(self_ids)
        return []






class _FakeTime:
    """Records every backoff sleep and every attempt bound; a hung attempt is
    abandoned at its bound on THIS clock, not the event loop's."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []
        self.timeouts: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    async def wait_for(self, awaitable, *, timeout: float):  # noqa: ASYNC109 — mirrors asyncio.wait_for
        self.timeouts.append(timeout)
        task = asyncio.ensure_future(awaitable)
        await _settle()
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            self.now += timeout
            raise TimeoutError
        return task.result()




async def test_platform_state_gauge_is_one_hot_and_follows_promotion():
    """`nora_platform_state` is one-hot over `PlatformState` for every table
    platform — `disabled` included — at build, and moves on promotion."""
    from types import SimpleNamespace

    from nora.platform.registry import PLATFORMS, Built, PlatformEntry

    fake = FakePlatformAdapter(identities=[RuntimeError("down"), IDENTITY])

    async def build(section, ctx):
        return Built(fake, bridge=False)

    off = SimpleNamespace(enabled=False)
    cfg = SimpleNamespace(signal=off, discord=off, whatsapp=off, instagram=off,
                          telegram=SimpleNamespace(enabled=True))
    metrics = Metrics()
    registry = await AdapterRegistry.from_config(
        cfg, metrics=metrics, table=(*PLATFORMS, PlatformEntry("telegram", build)))

    def states() -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for (name, labels), value in metrics.snapshot().items():
            if name == "nora_platform_state":
                d = dict(labels)
                out.setdefault(d["platform"], {})[d["state"]] = value
        return out

    expected_off = {"live": 0.0, "degraded": 0.0, "disabled": 1.0}
    assert states() == {
        "signal": expected_off, "discord": expected_off, "whatsapp": expected_off,
        "instagram": expected_off,
        "telegram": {"live": 0.0, "degraded": 1.0, "disabled": 0.0}}
    await registry.recover("telegram", sleep=_no_wait)
    assert states()["telegram"] == {"live": 1.0, "degraded": 0.0, "disabled": 0.0}
