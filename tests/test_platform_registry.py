"""`AdapterRegistry.from_config` — builds enabled adapters and resolves each
one's own identity at startup. An identity failure degrades THAT platform
alone; misconfiguration still refuses to start
(docs/design/platform-bridges/platform-liveness.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from aiohttp import web

from nora.domain.ports import PlatformIdentity
from nora.obs.metrics import Metrics
from nora.platform.discord.adapter import DiscordAdapter
from nora.platform.instagram.adapter import InstagramAdapter
from nora.platform.registry import AdapterRegistry, AdapterRegistryError
from nora.platform.signal.adapter import SignalAdapter
from nora.platform.whatsapp.adapter import WhatsAppAdapter


@dataclass(frozen=True)
class _SignalCfg:
    base_url: str
    account: str
    enabled: bool


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
    signal: _SignalCfg
    discord: _DiscordCfg = field(default_factory=_DiscordCfg)
    whatsapp: _WhatsAppCfg = field(default_factory=_WhatsAppCfg)
    instagram: _InstagramCfg = field(default_factory=_InstagramCfg)


async def _accounts_server(aiohttp_server, payload):
    async def handler(request):
        return web.json_response(payload)

    app = web.Application()
    app.router.add_get("/api/v1/accounts", handler)
    return await aiohttp_server(app)


async def test_an_enabled_platform_is_built_and_its_identity_resolved(aiohttp_server):
    server = await _accounts_server(aiohttp_server, [{"number": "+852999"}])
    base_url = str(server.make_url("")).rstrip("/")
    cfg = _PlatformsCfg(signal=_SignalCfg(base_url=base_url, account="+852999", enabled=True))

    registry = await AdapterRegistry.from_config(cfg, metrics=Metrics())

    assert "signal" in registry
    assert len(registry) == 1
    assert isinstance(registry.get("signal"), SignalAdapter)
    identity = registry.identity_for("signal")
    assert identity.platform == "signal"
    assert identity.primary == "+852999"


async def test_a_disabled_platform_is_built_by_no_one():
    cfg = _PlatformsCfg(
        signal=_SignalCfg(base_url="http://127.0.0.1:1", account="+852999", enabled=False)
    )

    registry = await AdapterRegistry.from_config(cfg, metrics=Metrics())

    assert "signal" not in registry
    assert len(registry) == 0


async def test_an_unreachable_daemon_fails_fast_with_a_readable_message():
    """`derive_account` cannot determine who Nora is without the daemon — a
    startup failure, not a degraded start (R-12 depends on identity being
    known)."""
    cfg = _PlatformsCfg(
        signal=_SignalCfg(base_url="http://127.0.0.1:1", account="", enabled=True)
    )

    with pytest.raises(AdapterRegistryError, match="account"):
        await AdapterRegistry.from_config(cfg, metrics=Metrics())


async def test_a_configured_account_override_skips_the_daemon_round_trip():
    """`account` set in config short-circuits `derive_account` entirely, so
    this never needs a reachable daemon for the account half — only
    `resolve_identity()`'s best-effort alias lookup touches the network, and
    that degrades to `{primary}` rather than failing (see
    `nora.platform.signal.account.derive_aliases`)."""
    cfg = _PlatformsCfg(
        signal=_SignalCfg(base_url="http://127.0.0.1:1", account="+852999", enabled=True)
    )

    registry = await AdapterRegistry.from_config(cfg, metrics=Metrics())

    assert registry.identity_for("signal").primary == "+852999"


async def test_a_signal_identity_failure_degrades_signal_not_the_registry(monkeypatch):
    """An identity failure is unavailability, not misconfiguration: the
    registry returns with Signal DEGRADED — present, latched, and with no
    `identities` entry (no placeholder for the self-tag check to match against)."""

    async def _boom(self):
        raise RuntimeError("daemon returned garbage")

    monkeypatch.setattr(SignalAdapter, "resolve_identity", _boom)
    cfg = _PlatformsCfg(
        signal=_SignalCfg(base_url="http://127.0.0.1:1", account="+852999", enabled=True)
    )

    registry = await AdapterRegistry.from_config(cfg, metrics=Metrics())

    assert "signal" in registry and registry.get("signal").degraded
    assert "signal" not in registry.identities
    assert registry.state_of("signal") == "degraded"


async def test_a_disabled_discord_platform_is_built_by_no_one():
    cfg = _PlatformsCfg(
        signal=_SignalCfg(base_url="http://127.0.0.1:1", account="+852999", enabled=False),
        discord=_DiscordCfg(enabled=False),
    )

    registry = await AdapterRegistry.from_config(cfg, metrics=Metrics())

    assert "discord" not in registry


async def test_an_enabled_discord_platform_with_no_token_fails_fast():
    """Fail-fast is the point (module docstring) — an enabled platform with
    an unresolvable secret must not silently build a broken adapter."""
    cfg = _PlatformsCfg(
        signal=_SignalCfg(base_url="http://127.0.0.1:1", account="+852999", enabled=False),
        discord=_DiscordCfg(enabled=True),
    )

    with pytest.raises(AdapterRegistryError, match="token"):
        await AdapterRegistry.from_config(cfg, metrics=Metrics(), discord_token="")


async def test_an_enabled_discord_platform_is_built_and_its_identity_resolved(monkeypatch):
    """Mirrors test_resolve_identity_failing_fails_the_whole_registry's technique
    (monkeypatching resolve_identity) rather than a fake HTTP server: Discord's
    REST base URL is not an operator-configurable production field the way
    Signal's daemon address is, so there is nothing to point at a fake server."""

    async def _fake_resolve(self):
        return PlatformIdentity(platform="discord", primary="555")

    monkeypatch.setattr(DiscordAdapter, "resolve_identity", _fake_resolve)
    cfg = _PlatformsCfg(
        signal=_SignalCfg(base_url="http://127.0.0.1:1", account="+852999", enabled=False),
        discord=_DiscordCfg(enabled=True),
    )

    registry = await AdapterRegistry.from_config(
        cfg, metrics=Metrics(), discord_token="tok"  # noqa: S106 -- test fixture value
    )

    assert "discord" in registry
    assert isinstance(registry.get("discord"), DiscordAdapter)
    assert registry.identity_for("discord").primary == "555"


async def test_a_discord_identity_failure_degrades_discord_alone(monkeypatch):
    async def _boom(self):
        raise RuntimeError("discord returned garbage")

    monkeypatch.setattr(DiscordAdapter, "resolve_identity", _boom)
    cfg = _PlatformsCfg(
        signal=_SignalCfg(base_url="http://127.0.0.1:1", account="+852999", enabled=False),
        discord=_DiscordCfg(enabled=True),
    )

    registry = await AdapterRegistry.from_config(
        cfg, metrics=Metrics(), discord_token="tok"  # noqa: S106 -- test fixture value
    )

    assert registry.state_of("discord") == "degraded"
    assert "discord" not in registry.identities


async def _whatsapp_self_server(aiohttp_server, *, status: int = 200):
    """A stand-in for the bridge's `GET /self`."""

    async def handler(request):
        if status != 200:
            return web.json_response({"error": "not_connected"}, status=status)
        return web.json_response({"phone": "85299900000", "lid": "60000000000001"})

    app = web.Application()
    app.router.add_get("/self", handler)
    return await aiohttp_server(app)


async def test_a_disabled_whatsapp_platform_is_built_by_no_one():
    cfg = _PlatformsCfg(
        signal=_SignalCfg(base_url="http://127.0.0.1:1", account="+852999", enabled=False),
        whatsapp=_WhatsAppCfg(enabled=False),
    )

    registry = await AdapterRegistry.from_config(cfg, metrics=Metrics())

    assert "whatsapp" not in registry


async def test_an_enabled_whatsapp_platform_is_built_and_its_identity_resolved(
    aiohttp_server,
):
    """Unlike Discord there is no token step: the bridge holds the session, so
    the whole build is `GET /self` against it — pointed at a real (test) HTTP
    server rather than monkeypatched, because `base_url` IS the
    operator-configurable field here."""
    server = await _whatsapp_self_server(aiohttp_server)
    base_url = str(server.make_url("")).rstrip("/")
    cfg = _PlatformsCfg(
        signal=_SignalCfg(base_url="http://127.0.0.1:1", account="+852999", enabled=False),
        whatsapp=_WhatsAppCfg(enabled=True, base_url=base_url),
    )

    registry = await AdapterRegistry.from_config(cfg, metrics=Metrics())

    assert "whatsapp" in registry
    assert isinstance(registry.get("whatsapp"), WhatsAppAdapter)
    identity = registry.identity_for("whatsapp")
    assert identity.primary == "+85299900000"
    # BOTH id forms are aliases — the MentionPolicy membership test must work
    # whether a group mention arrives as the bot's phone or its lid.
    assert identity.aliases >= {"+85299900000", "60000000000001"}


async def test_an_unpaired_whatsapp_degrades_whatsapp_alone(aiohttp_server):
    """`GET /self` answering 503 (not paired / socket down) degrades WhatsApp:
    built, latched, no identity — and recovered later without a restart."""
    server = await _whatsapp_self_server(aiohttp_server, status=503)
    base_url = str(server.make_url("")).rstrip("/")
    cfg = _PlatformsCfg(
        signal=_SignalCfg(base_url="http://127.0.0.1:1", account="+852999", enabled=False),
        whatsapp=_WhatsAppCfg(enabled=True, base_url=base_url),
    )

    registry = await AdapterRegistry.from_config(cfg, metrics=Metrics())

    assert registry.state_of("whatsapp") == "degraded"
    assert "whatsapp" not in registry.identities


async def test_mention_match_set_covers_every_platform_not_just_signal():
    """`@Nora hi` on Discord must be recognised as a mention of her.

    `MentionPolicy`/`ReplyToNoraPolicy` take their match set from the registry.
    Built from `registry.identity_for("signal").aliases` alone it would hold a
    phone number and an ACI while a Discord mention carries a 17-19 digit
    snowflake. The parser fills `mentions` correctly, so nothing downstream
    could tell this from "nobody tagged her" — the ingest-attribution-silence
    failure `MentionPolicy`'s own docstring says it exists to end, reintroduced
    by a second platform.

    Asserts the UNION, and asserts each platform's id is in it: a set built
    from either platform alone passes one half and fails the other.
    """
    from nora.domain.identity import Identifier, PlatformIdentity

    signal = PlatformIdentity(
        platform="signal", primary="+85290000100",
        identifiers=frozenset({Identifier("signal", "phone", "+85290000100")}),
    )
    discord = PlatformIdentity(
        platform="discord", primary="100000000000000100",
        identifiers=frozenset({Identifier("discord", "user_id", "100000000000000100")}),
    )
    registry = AdapterRegistry(
        adapters={}, identities={"signal": signal, "discord": discord},
    )

    # THE PRODUCTION EXPRESSION, called — not a copy of it re-typed here.
    # A union rebuilt in the test body and asserted about would measure itself
    # and stay green with the production code reverted.
    self_aliases = registry.self_aliases()

    assert "100000000000000100" in self_aliases, (
        "a Discord @mention must be able to match Nora's own id"
    )
    assert "+85290000100" in self_aliases, "Signal must not have been dropped"
    # No handle can be in here to begin with: identity is matched on IMMUTABLE
    # ids only, enforced one level down in
    # `nora.domain.identity` — `username` is not a matchable kind on any
    # platform, so `Identifier` refuses to carry one and no `aliases` set can
    # contain one. Pinned by `tests/unit/test_domain_identity.py::
    # test_a_username_is_never_a_matchable_kind_on_any_platform`.


# --- instagram ---------------------------------------------------------------
#
# The behaviour itself — degrade rather than refuse, and everything "degraded"
# is defined to mean — lives in `tests/unit/test_instagram_degradation.py`.
# What belongs HERE is the property this module is about: that the fourth
# `if platforms.<x>.enabled:` block is the one that does not fail fast, and
# that its exception does not leak into the other three.


async def _instagram_health_server(aiohttp_server, *, session: str = "authenticated"):
    """A stand-in for the bridge's `GET /health`, which carries identity.

    It answers **200 even for a checkpoint**, because the real one does — an
    Instagram challenge is a normal operating state that the bridge describes
    rather than crashes on. That is what makes this a model of the server and
    not a switch: a fake that returned a 5xx for a checkpoint would let an
    adapter that ignored `session` pass.
    """
    async def handler(request):
        return web.json_response({
            "ok": True, "session": session, "connected": True, "seq": 1,
            "self": {"fbid": "18247361241432477", "igid": "79597894900",
                     "username": "not_____nora"},
        })

    app = web.Application()
    app.router.add_get("/health", handler)
    return await aiohttp_server(app)


async def test_a_disabled_instagram_platform_is_built_by_no_one():
    cfg = _PlatformsCfg(
        signal=_SignalCfg(base_url="http://127.0.0.1:1", account="+852999", enabled=False),
        instagram=_InstagramCfg(enabled=False),
    )

    registry = await AdapterRegistry.from_config(cfg, metrics=Metrics())

    assert "instagram" not in registry


async def test_an_enabled_instagram_platform_is_built_and_its_identity_resolved(
    aiohttp_server,
):
    server = await _instagram_health_server(aiohttp_server)
    base_url = str(server.make_url("")).rstrip("/")
    cfg = _PlatformsCfg(
        signal=_SignalCfg(base_url="http://127.0.0.1:1", account="+852999", enabled=False),
        instagram=_InstagramCfg(enabled=True, base_url=base_url),
    )

    registry = await AdapterRegistry.from_config(cfg, metrics=Metrics())

    assert "instagram" in registry
    assert isinstance(registry.get("instagram"), InstagramAdapter)
    identity = registry.identity_for("instagram")
    assert identity.platform == "instagram"
    # The interop fbid, which is what a mention carries — NOT the igid, and
    # not `viewer.GetFBID()`, both of which are different numbers for the same
    # account and match nothing.
    assert identity.primary == "18247361241432477"


async def test_a_CHECKPOINTED_instagram_does_NOT_fail_the_whole_registry(aiohttp_server):
    """The other three blocks raise `AdapterRegistryError` here; this one must
    not, or one overnight captcha takes Signal and Discord down with it.

    Signal is ENABLED and healthy in this case on purpose: the assertion is
    not merely that the call returned, it is that the OTHER platform survived.
    """
    accounts = await _accounts_server(aiohttp_server, [{"number": "+852999"}])
    instagram = await _instagram_health_server(aiohttp_server, session="checkpointed")
    cfg = _PlatformsCfg(
        signal=_SignalCfg(
            base_url=str(accounts.make_url("")).rstrip("/"), account="+852999", enabled=True,
        ),
        instagram=_InstagramCfg(
            enabled=True, base_url=str(instagram.make_url("")).rstrip("/"),
        ),
    )

    registry = await AdapterRegistry.from_config(cfg, metrics=Metrics())

    assert "signal" in registry
    assert registry.identity_for("signal").primary == "+852999"
    assert "instagram" in registry
    assert registry.get("instagram").degraded is True


# --- per-platform liveness (docs/design/platform-bridges/platform-liveness.md) --


class _Clock:
    """Fake time for the bounded bridge wait: `sleep` advances `now`."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += max(seconds, 0.0)


def _bridge_cfg(base_url: str, *, enabled: bool = True, **extra):
    from types import SimpleNamespace

    return SimpleNamespace(enabled=enabled, mode="bridge", base_url=base_url,
                           account="", token_env="DISCORD_BOT_TOKEN",  # noqa: S106 — env NAME
                           **extra)


async def _bridges_server(aiohttp_server, answers: dict[str, int]):
    """A fake bridge per platform: `/<platform>/v1/self` answers and counts."""
    calls: dict[str, int] = dict.fromkeys(answers, 0)
    identities = {
        "signal": {"primary": "+852999", "aliases": ["+852999", "aci-nora"]},
        "discord": {"primary": "111111111111111111"},
        "whatsapp": {"primary": "+85299900000", "aliases": ["60000000000001"]},
        "instagram": {"primary": "18247361241432477"},
    }

    def _handler(platform: str):
        async def handler(request):
            calls[platform] += 1
            status = answers[platform]
            if status != 200:
                return web.json_response({"error": "unavailable"}, status=status)
            return web.json_response(identities[platform])
        return handler

    app = web.Application()
    for platform in answers:
        app.router.add_get(f"/{platform}/v1/self", _handler(platform))
    server = await aiohttp_server(app)
    return server, calls


async def test_an_identity_failure_degrades_that_platform_and_the_rest_start_live(
        aiohttp_server):
    """The WhatsApp-logout incident, replayed: the WhatsApp bridge's
    `/v1/self` stays unavailable through the whole bounded wait. The registry
    RETURNS — Signal and Discord live with their identities, WhatsApp degraded:
    built, latched, absent from `identities` and from the self-alias set."""
    from types import SimpleNamespace

    server, _ = await _bridges_server(
        aiohttp_server, {"signal": 200, "discord": 200, "whatsapp": 503})
    url = str(server.make_url("")).rstrip("/")
    cfg = SimpleNamespace(
        signal=_bridge_cfg(f"{url}/signal"), discord=_bridge_cfg(f"{url}/discord"),
        whatsapp=_bridge_cfg(f"{url}/whatsapp"),
        instagram=_bridge_cfg(f"{url}/instagram", enabled=False))
    clock = _Clock()

    registry = await AdapterRegistry.from_config(
        cfg, metrics=Metrics(), bridge_token="t",  # noqa: S106 — test value
        sleep=clock.sleep, clock=clock)

    assert registry.state_of("signal") == "live"
    assert registry.state_of("discord") == "live"
    assert registry.identity_for("signal").primary == "+852999"
    assert registry.identity_for("discord").primary == "111111111111111111"
    assert registry.state_of("whatsapp") == "degraded"
    assert "whatsapp" in registry and registry.get("whatsapp").degraded
    assert "whatsapp" not in registry.identities
    aliases = registry.self_aliases()
    assert "+852999" in aliases and "111111111111111111" in aliases
    assert "+85299900000" not in aliases and "60000000000001" not in aliases
    assert registry.state_of("instagram") == "disabled"
    for adapter in registry:
        await adapter.close()


async def _refused_bridge(aiohttp_server, status):
    from types import SimpleNamespace

    server, calls = await _bridges_server(aiohttp_server, {"whatsapp": status})
    url = str(server.make_url("")).rstrip("/")
    cfg = SimpleNamespace(
        signal=_bridge_cfg("", enabled=False), discord=_bridge_cfg("", enabled=False),
        whatsapp=_bridge_cfg(f"{url}/whatsapp"), instagram=_bridge_cfg("", enabled=False))
    clock = _Clock()
    try:
        await AdapterRegistry.from_config(
            cfg, metrics=Metrics(), bridge_token="t",  # noqa: S106 — test value
            sleep=clock.sleep, clock=clock)
    finally:
        # Refused on the FIRST answer: never waited on, never retried.
        assert calls["whatsapp"] == 1


async def _missing_discord_token(aiohttp_server):
    cfg = _PlatformsCfg(
        signal=_SignalCfg(base_url="http://127.0.0.1:1", account="+852999", enabled=False),
        discord=_DiscordCfg(enabled=True))
    await AdapterRegistry.from_config(cfg, metrics=Metrics(), discord_token="")


async def _underivable_signal_account(aiohttp_server):
    cfg = _PlatformsCfg(signal=_SignalCfg(base_url="http://127.0.0.1:1", account="",
                                          enabled=True))
    await AdapterRegistry.from_config(cfg, metrics=Metrics())


async def _unknown_mode(aiohttp_server):
    from nora.config import WhatsAppPlatformConfig

    WhatsAppPlatformConfig(enabled=True, mode="brdige")


@pytest.mark.parametrize(("case", "error"), [
    (_missing_discord_token, "token"),
    (_underivable_signal_account, "account"),
    (lambda server: _refused_bridge(server, 401), "refused"),
    (lambda server: _refused_bridge(server, 403), "refused"),
    (_unknown_mode, "mode"),
], ids=["discord-token", "signal-account", "bridge-401", "bridge-403", "unknown-mode"])
async def test_misconfiguration_still_refuses_to_start(aiohttp_server, case, error):
    """Misconfiguration is FATAL, never degraded: a missing token, an
    underivable Signal account, a bridge answering 401/403 to Nora's own
    credentials (refused on the first answer, no wait, no retry), and an
    unknown `mode` (refused by config load, so it never reaches the registry).
    The bridge bearer and Redis URL are composition's own checks
    (`tests/test_composition.py`)."""
    from pydantic import ValidationError

    with pytest.raises((AdapterRegistryError, ValidationError), match=error):
        await case(aiohttp_server)


async def _inbound_rig(registry, database_url):
    """The telegram stream reader, and a runner over one older telegram row."""
    import asyncio
    import functools

    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import create_async_engine

    from nora.app.bridge_queue import DbSpeakingLocks, StreamReader, WorkRunner
    from nora.infra.sql.bridge_store import SqlBridgeStore
    from nora.infra.sql.schema import BRIDGE_WORK
    from tests.integration.test_platform_liveness import _CountingRedis, _ElectedStore

    redis = _CountingRedis()
    reader = asyncio.ensure_future(StreamReader(
        platform="telegram", account_key="a", redis=redis, store=_ElectedStore(),
        wait_live=functools.partial(registry.wait_live, "telegram")).run())
    engine = create_async_engine(database_url)
    store = SqlBridgeStore(engine)
    async with engine.begin() as conn:
        for platform in ("telegram", "sibling"):  # telegram's row is older
            await conn.execute(sa.insert(BRIDGE_WORK).values(
                kind="participation", ordinal=0, platform=platform,
                account_key="a", conversation_id="g", payload={}))
    runner = WorkRunner(store=store, redis=None, ingest=None, engagement=None,
                        slots=None, db_locks=DbSpeakingLocks(store), adapters={},
                        live_platforms=registry.live_platforms)
    return redis, reader, runner, engine


async def test_a_new_platform_needs_only_a_table_entry(aiohttp_client, clean_pg_url):
    """The abstraction, end to end. A test-only `telegram` row — a fake adapter
    on the shared `DegradeLatch` and one `PlatformEntry` — goes through boot
    degrade, the state gauge, `/health`, the gated listener, recovery and
    promotion with NO registry, health, metric or gate code written for it."""
    import asyncio
    import functools
    from pathlib import Path
    from types import SimpleNamespace

    from nora.__main__ import _listen_when_live
    from nora.app import runtime
    from nora.doctrine.loader import DoctrineStore
    from nora.domain.models import ConversationTarget
    from nora.obs.health import LLMState, build_health_app
    from nora.platform.registry import PLATFORMS, Built, PlatformEntry
    from nora.testing import FakeMemoryStore, FakePlatformAdapter, FakeRecordStore
    from tests.fakes import SinkSender

    identity = PlatformIdentity(platform="telegram", primary="tg-nora")
    fake = FakePlatformAdapter(
        "telegram", identities=[RuntimeError("logged out"), identity])
    sibling = FakePlatformAdapter("sibling")  # a live row beside it in the table

    async def build(section, ctx):
        return Built(fake, bridge=False)

    async def build_sibling(section, ctx):
        return Built(sibling, bridge=False)

    table = (*PLATFORMS, PlatformEntry("telegram", build),
             PlatformEntry("sibling", build_sibling))
    cfg = SimpleNamespace(
        signal=_SignalCfg(base_url="", account="", enabled=False),
        discord=_DiscordCfg(), whatsapp=_WhatsAppCfg(), instagram=_InstagramCfg(),
        telegram=SimpleNamespace(enabled=True), sibling=SimpleNamespace(enabled=True))
    metrics = Metrics()

    registry = await AdapterRegistry.from_config(cfg, metrics=metrics, table=table)

    # Boot: degraded, gauged, reported — and the latch answered health.
    assert registry.state_of("telegram") == "degraded"
    assert "tg-nora" not in registry.self_aliases()

    def gauge(state):
        return metrics.snapshot()[("nora_platform_state",
                                   (("platform", "telegram"), ("state", state)))]

    assert (gauge("live"), gauge("degraded"), gauge("disabled")) == (0.0, 1.0, 0.0)
    app = build_health_app(
        adapter=SinkSender(), adapters=registry.adapters, registry=registry,
        store=FakeRecordStore(), honcho=FakeMemoryStore(), metrics=metrics,
        doctrine_store=DoctrineStore(Path("nora/doctrine"), canary="C", metrics=metrics),
        started_at=0.0, llm_state=LLMState())
    client = await aiohttp_client(app)
    entry = (await (await client.get("/health")).json())["platforms"]["telegram"]
    assert entry["state"] == "degraded" and entry["degraded"] is True
    assert fake.health_calls == 0

    # The gated listener does not start; a send is refused before the fake.
    seen: list[bool] = []
    fake.on_first_listen = lambda: seen.append(
        registry.identities.get("telegram") == identity
        and "tg-nora" in registry.self_aliases())

    async def _ignore(message):
        return None

    task = asyncio.ensure_future(runtime.supervise_adapter(runtime.SupervisedAdapter(
        platform="telegram", listen=functools.partial(
            _listen_when_live, registry, fake, {"on_message": _ignore})), metrics))
    target = ConversationTarget(platform="telegram", conversation_id="g",
                                conversation_type="group")
    assert not (await fake.send(target=target, text="hi")).ok
    # Its stream reader makes no stream call, and the work runner claims the
    # live sibling's row first and never the OLDER degraded row (real SQL).
    redis, reader, runner, engine = await _inbound_rig(registry, clean_pg_url)
    lane = frozenset({"participation"})
    for _ in range(5):
        await asyncio.sleep(0)
    assert fake.listen_calls == 0 and fake.sent == [] and redis.calls == []
    first = await runner._claim_next(lane)
    assert first is not None and first.platform == "sibling"
    assert await runner._claim_next(lane) is None

    # Recovery promotes it: identity first, then the listener opens.
    async def no_wait(seconds):
        return None

    await registry.recover("telegram", sleep=no_wait)
    for _ in range(5):
        await asyncio.sleep(0)
    assert seen == [True]
    assert redis.calls, "the reader made its first stream call once live"
    promoted = await runner._claim_next(lane)
    assert promoted is not None and promoted.platform == "telegram"
    assert registry.state_of("telegram") == "live"
    assert (gauge("live"), gauge("degraded")) == (1.0, 0.0)
    assert (await fake.send(target=target, text="hi")).ok and len(fake.sent) == 1
    entry = (await (await client.get("/health")).json())["platforms"]["telegram"]
    assert entry["state"] == "live" and entry["account"] == "tg-nora"
    task.cancel()
    await asyncio.gather(task, reader, return_exceptions=True)
    await engine.dispose()
