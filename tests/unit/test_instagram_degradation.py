"""An enabled Instagram adapter that cannot authenticate is REGISTERED
DEGRADED, not refused.

The failure this exists to prevent has two halves and both are asserted here:

1. **One Instagram checkpoint must not stop Nora starting.** `from_config` is
   fail-fast by design, which is right where "cannot resolve my own identity"
   means somebody mistyped a token. On Instagram it means a captcha happened
   overnight, and under the unmodified rule Signal and Discord go down with it.
2. **A degraded adapter must not look like a quiet room.** It answers no
   sends, emits no events, and SAYS SO — at startup in the log and
   continuously on `/health`. A degradation nobody can read is worse than a
   refusal, because a refusal at least stops.

The stand-in bridge answers the real one's shapes: 200 with a cached `self`
block for a CHECKPOINTED session, which is the case a naive adapter passes.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.domain.models import ConversationTarget
from nora.obs.metrics import Metrics
from nora.platform.instagram.adapter import InstagramAdapter
from nora.platform.registry import AdapterRegistry

ROOM = "1183780976338188"
GROUP = ConversationTarget("instagram", ROOM, "group")
NORA_FBID = "18247361241432477"


# --- config stand-ins, structurally satisfying `_PlatformsConfig` -----------


@dataclass(frozen=True)
class _SignalCfg:
    base_url: str = "http://127.0.0.1:1"
    account: str = "+15550000000"
    enabled: bool = False


@dataclass(frozen=True)
class _DiscordCfg:
    enabled: bool = False
    token_env: str = "DISCORD_BOT_TOKEN"  # noqa: S105 — an env-var NAME


@dataclass(frozen=True)
class _WhatsAppCfg:
    enabled: bool = False
    base_url: str = "http://127.0.0.1:1"


@dataclass(frozen=True)
class _InstagramCfg:
    enabled: bool = False
    base_url: str = "http://127.0.0.1:1"


@dataclass(frozen=True)
class _PlatformsCfg:
    signal: _SignalCfg = field(default_factory=_SignalCfg)
    discord: _DiscordCfg = field(default_factory=_DiscordCfg)
    whatsapp: _WhatsAppCfg = field(default_factory=_WhatsAppCfg)
    instagram: _InstagramCfg = field(default_factory=_InstagramCfg)


@pytest.fixture
async def bridge():
    """`(box, base_url)` — an instagram-bridge whose `/health` says whatever
    the test needs, including the 200-with-a-checkpoint the real one serves.

    It also serves the message and reaction routes **successfully**, and records every
    call. That is what makes the refusal tests below bite: pointed at a dead
    port, a degraded adapter that had LOST its refusal would still answer
    `ok=False` — from the connection error — and the assertion would pass
    while measuring nothing. Against a bridge that says yes, only the refusal
    itself can produce a failure, and `box["calls"]` proves nothing left the
    process.
    """
    box: dict = {
        "calls": [],
        "body": {
            "ok": True, "session": "authenticated", "connected": True, "seq": 1,
            "self": {"fbid": NORA_FBID, "igid": "79597894900", "username": "not_____nora"},
        },
    }

    async def health(request: web.Request) -> web.Response:
        return web.json_response(box["body"])

    async def accept(request: web.Request) -> web.Response:
        box["calls"].append(request.path)
        if request.path.endswith("/reaction"):
            return web.json_response({"confirmed": True})
        return web.json_response({"id": "mid.$x", "timestamp_ms": 1})

    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_post("/threads/{thread_fbid}/messages", accept)
    app.router.add_put("/threads/{thread_fbid}/messages/{message_id}/reaction", accept)
    server = TestServer(app)
    await server.start_server()
    yield box, str(server.make_url("")).rstrip("/")
    await server.close()


# --- the registry: degrade, never refuse ------------------------------------


async def test_an_authenticated_bridge_registers_NORMALLY(bridge):
    """The positive control. Without it, a registry that degraded
    unconditionally would pass every test below."""
    _box, base_url = bridge
    cfg = _PlatformsCfg(instagram=_InstagramCfg(enabled=True, base_url=base_url))

    registry = await AdapterRegistry.from_config(cfg, metrics=Metrics())

    assert "instagram" in registry
    adapter = registry.get("instagram")
    assert isinstance(adapter, InstagramAdapter)
    assert adapter.degraded is False
    assert registry.identity_for("instagram").primary == NORA_FBID
    assert NORA_FBID in registry.self_aliases()


async def test_a_CHECKPOINTED_bridge_still_lets_the_registry_BUILD(bridge, caplog):
    """The whole point. This raises `AdapterRegistryError` -> `StartupError`
    for Signal and Discord, and must not here."""
    box, base_url = bridge
    box["body"] = {**box["body"], "session": "checkpointed", "detail": "challenge required"}
    cfg = _PlatformsCfg(instagram=_InstagramCfg(enabled=True, base_url=base_url))

    with caplog.at_level(logging.ERROR, logger="nora.instagram.adapter"):
        registry = await AdapterRegistry.from_config(cfg, metrics=Metrics())

    assert "instagram" in registry
    assert registry.get("instagram").degraded is True
    # VISIBLE: the startup half. The line names the state AND says the other
    # platforms are unaffected, because that is the question an operator
    # reading it at 8am is actually asking.
    assert "DEGRADED" in caplog.text
    assert "checkpointed" in caplog.text


async def test_an_UNREACHABLE_bridge_degrades_rather_than_refusing_to_start():
    """A container that has not come up yet is the same class of event as a
    checkpoint: it is not a misconfiguration, and Signal has done nothing."""
    cfg = _PlatformsCfg(instagram=_InstagramCfg(enabled=True, base_url="http://127.0.0.1:1"))

    registry = await AdapterRegistry.from_config(cfg, metrics=Metrics())

    assert registry.get("instagram").degraded is True


async def test_a_DEGRADED_adapter_contributes_NO_self_alias(bridge):
    """The trap a placeholder identity would walk into. `PlatformIdentity`
    derives `aliases` from `primary`, and `self_aliases()` is a flat union —
    so registering `primary=""` to keep the mapping rectangular would put `""`
    into the very set `MentionPolicy` tests membership in."""
    box, base_url = bridge
    box["body"] = {**box["body"], "session": "checkpointed"}
    cfg = _PlatformsCfg(instagram=_InstagramCfg(enabled=True, base_url=base_url))

    registry = await AdapterRegistry.from_config(cfg, metrics=Metrics())

    assert "instagram" not in registry.identities
    assert registry.self_aliases() == frozenset()
    assert "" not in registry.self_aliases()


async def test_a_DISABLED_instagram_is_built_by_no_one():
    registry = await AdapterRegistry.from_config(_PlatformsCfg(), metrics=Metrics())

    assert "instagram" not in registry
    assert len(registry) == 0


# --- the adapter: what "degraded" is defined to mean -------------------------


def _degraded_adapter(base_url: str = "http://127.0.0.1:1") -> InstagramAdapter:
    adapter = InstagramAdapter(base_url)
    adapter.degrade("InstagramIdentityError: session is 'checkpointed'")
    return adapter


async def test_a_degraded_adapter_REFUSES_TO_SEND_without_an_http_call(bridge):
    """Against a bridge that would ACCEPT the send — see the fixture. Not
    retryable either: a checkpoint is cleared by a human, and a caller that
    backed off and retried would hammer a challenged account, which is how a
    captcha becomes a ban."""
    box, base_url = bridge
    adapter = _degraded_adapter(base_url)

    result = await adapter.send(target=GROUP, text="hello")

    assert result.ok is False
    assert result.retryable is False
    assert box["calls"] == [], "the refusal must not reach the wire"


async def test_a_degraded_adapter_places_NO_reaction(bridge):
    box, base_url = bridge
    adapter = _degraded_adapter(base_url)

    result = await adapter.react(
        target=GROUP, emoji="❤️", to_author="x", to_timestamp=1, to_message_id="mid.$a",
    )

    assert result.ok is False
    assert result.supported is False
    assert box["calls"] == []


async def test_a_degraded_adapter_EMITS_NO_EVENTS_and_NEVER_RETURNS():
    """Never returns, and that is not pedantry: `supervise_adapter` re-calls
    `listen()` immediately on a clean return, so an adapter that returned
    would become a hot loop with no error anywhere."""
    adapter = _degraded_adapter()
    got: list = []

    async def on_message(message):
        got.append(message)

    task = asyncio.create_task(adapter.listen(on_message=on_message))
    await asyncio.sleep(0.05)

    assert not task.done()
    assert got == []
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_a_degraded_adapter_SAYS_SO_on_health():
    """The continuous half of visibility. A log line scrolls away; a
    health field says what is true now."""
    adapter = _degraded_adapter()

    health = await adapter.health()

    assert health.platform == "instagram"
    assert health.degraded is True
    assert "checkpointed" in health.detail


async def test_a_HEALTHY_adapter_reports_no_degradation():
    adapter = InstagramAdapter("http://127.0.0.1:1")

    health = await adapter.health()

    assert health.degraded is False
    assert health.detail == ""


async def test_a_session_checkpointed_AFTER_startup_degrades_the_LIVE_adapter(bridge):
    """Startup and runtime are two ways to lose the session and `from_config`
    only sees one of them. Without this, an adapter that was authenticated at
    boot and checkpointed at 4am reads as connected and merely quiet.

    The bridge here ACCEPTS sends, so the refusal below is the adapter's own
    and not a connection error wearing its clothes."""
    _box, base_url = bridge
    adapter = InstagramAdapter(base_url)
    assert adapter.degraded is False
    assert (await adapter.send(target=GROUP, text="before")).ok is True

    adapter._listener._absorb_heartbeat(
        {"kind": "heartbeat", "ts_ms": 1, "session": "checkpointed"}
    )

    assert adapter.degraded is True
    assert (await adapter.health()).degraded is True
    assert (await adapter.send(target=GROUP, text="after")).ok is False
