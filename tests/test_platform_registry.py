"""`AdapterRegistry.from_config` — builds enabled adapters and resolves each
one's own identity at startup, failing fast.
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


async def test_resolve_identity_failing_fails_the_whole_registry(monkeypatch):
    """PROBE-adjacent: even though `resolve_identity()` doesn't raise in
    practice today (`derive_aliases` is best-effort), the registry still
    wraps the call in its own try/except — this proves that wrapping
    actually routes a hypothetical failure through `AdapterRegistryError`
    rather than letting it propagate raw."""

    async def _boom(self):
        raise RuntimeError("daemon returned garbage")

    monkeypatch.setattr(SignalAdapter, "resolve_identity", _boom)
    cfg = _PlatformsCfg(
        signal=_SignalCfg(base_url="http://127.0.0.1:1", account="+852999", enabled=True)
    )

    with pytest.raises(AdapterRegistryError, match="identity"):
        await AdapterRegistry.from_config(cfg, metrics=Metrics())


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


async def test_discord_resolve_identity_failing_fails_the_whole_registry(monkeypatch):
    async def _boom(self):
        raise RuntimeError("discord returned garbage")

    monkeypatch.setattr(DiscordAdapter, "resolve_identity", _boom)
    cfg = _PlatformsCfg(
        signal=_SignalCfg(base_url="http://127.0.0.1:1", account="+852999", enabled=False),
        discord=_DiscordCfg(enabled=True),
    )

    with pytest.raises(AdapterRegistryError, match="identity"):
        await AdapterRegistry.from_config(
            cfg, metrics=Metrics(), discord_token="tok"  # noqa: S106 -- test fixture value
        )


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


async def test_an_unpaired_bridge_fails_fast(aiohttp_server):
    """`GET /self` answering 503 (not paired / socket down) is a startup
    failure, not a deaf adapter allowed to build."""
    server = await _whatsapp_self_server(aiohttp_server, status=503)
    base_url = str(server.make_url("")).rstrip("/")
    cfg = _PlatformsCfg(
        signal=_SignalCfg(base_url="http://127.0.0.1:1", account="+852999", enabled=False),
        whatsapp=_WhatsAppCfg(enabled=True, base_url=base_url),
    )

    with pytest.raises(AdapterRegistryError, match="identity"):
        await AdapterRegistry.from_config(cfg, metrics=Metrics())


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
