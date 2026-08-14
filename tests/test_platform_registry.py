"""`AdapterRegistry.from_config` — builds enabled adapters and resolves each
one's own identity at startup, failing fast (spec's platforms phase).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from aiohttp import web

from nora.domain.ports import PlatformIdentity
from nora.obs.metrics import Metrics
from nora.platform.discord.adapter import DiscordAdapter
from nora.platform.registry import AdapterRegistry, AdapterRegistryError
from nora.platform.signal.adapter import SignalAdapter


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
class _PlatformsCfg:
    signal: _SignalCfg
    discord: _DiscordCfg = field(default_factory=_DiscordCfg)


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


async def test_mention_match_set_covers_every_platform_not_just_signal():
    """The bug this pins, found live 2026-08-14: `@Nora hi` on Discord was
    ingested, attributed, and answered with silence.

    `MentionPolicy`/`ReplyToNoraPolicy` were built from
    `registry.identity_for("signal").aliases`, so the match set held a phone
    number and an ACI while a Discord mention carries a 17-19 digit snowflake.
    The parser was never at fault — it fills `mentions` correctly — so nothing
    downstream could tell this from "nobody tagged her". That is exactly the
    ingest-attribution-silence failure `MentionPolicy`'s own docstring says it
    exists to end, reintroduced by a second platform.

    Asserts the UNION, and asserts each platform's id is in it: a set built
    from either platform alone passes one half and fails the other.
    """
    from nora.app.composition import MUTABLE_ID_KINDS
    from nora.domain.identity import Identifier, PlatformIdentity

    signal = PlatformIdentity(
        platform="signal", primary="+85290000100",
        identifiers=frozenset({Identifier("signal", "phone", "+85290000100")}),
    )
    discord = PlatformIdentity(
        platform="discord", primary="100000000000000100",
        identifiers=frozenset({
            Identifier("discord", "user_id", "100000000000000100"),
            # `derive_identity` really does put this in, and `aliases` really
            # does flatten it in beside the snowflake — which is why the
            # assertion below is not hypothetical.
            Identifier("discord", "username", "\u0e17\u1d0f\u0280\u0251"),
        }),
    )
    registry = AdapterRegistry(
        adapters={}, identities={"signal": signal, "discord": discord},
    )

    self_aliases = frozenset(
        identifier.value
        for identity in registry.identities.values()
        for identifier in identity.identifiers
        if identifier.kind not in MUTABLE_ID_KINDS
    ) | frozenset(i.primary for i in registry.identities.values() if i.primary)

    assert "100000000000000100" in self_aliases, (
        "a Discord @mention must be able to match Nora's own id"
    )
    assert "+85290000100" in self_aliases, "Signal must not have been dropped"
    # Operator rule (2026-08-14): identity is matched on IMMUTABLE ids only.
    # A handle its owner can rename must never decide who Nora is — and
    # `PlatformIdentity.aliases` would have carried it, so this is what stops
    # a future matcher from silently arming that trap.
    assert "\u0e17\u1d0f\u0280\u0251" not in self_aliases, (
        "a mutable username must never be part of the identity match set"
    )
