"""composition step — `[identity.onboarding]` → `PlatformOnboarding`.

Rules are built ONLY when the managed backend is selected AND the operator
declared a collection; both conditions failing leaves `None` and the
runtime spawns nothing (the default file mode and empty config keep
today's behavior exactly — see `config_identity.OnboardingConfig`).

Each rule's discovery is platform-shaped: Discord gets the guild member
read (direct REST or the bridge's platform query), Signal the
`listGroups` member rows (direct RPC or the bridge's roster op), and a
platform that cannot enumerate a member set — Telegram's OIDC identity,
WhatsApp — refuses to BUILD rather than fabricate a roster. A rule that
survives to `OnboardingRule` inherits the provisioning service's own
actor, so every roster the runner pushes is admitted under the same
credential+scope check the private endpoint applies.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from nora.domain.managed import ObservedRows
from nora.domain.managed_ports import CollectionDiscovery
from nora.domain.management import Actor, ActorKind, Scope, ScopeKind
from nora.people import OnboardingRule, PlatformOnboarding, RosterProvisioning
from nora.platform.bridge.client import BridgeEndpoint, bridge_query
from nora.platform.discord.discovery import DiscordDiscovery
from nora.platform.discord.roster import DiscordRoster
from nora.platform.signal import directory
from nora.platform.signal.discovery import SignalDiscovery

from .startup import StartupError

log = logging.getLogger("nora.onboarding")


@dataclass(frozen=True)
class OnboardingWiring:
    onboarding: PlatformOnboarding | None


def onboarding(
    cfg: Any, *, managed_services: Any, discord_control: Any,
    discord_token: str,
) -> OnboardingWiring:
    config = cfg.identity.onboarding
    if cfg.identity.backend != "managed" or not config.collections:
        return OnboardingWiring(onboarding=None)
    services = managed_services
    rules = []
    for rule in config.collections:
        discovery = _discovery_for(
            rule.platform, cfg=cfg,
            discord_control=discord_control, discord_token=discord_token)
        if discovery is None:
            raise StartupError(
                f"onboarding collection {rule.name!r}: platform "
                f"{rule.platform!r} has no roster-discovery capability — "
                "only discord guilds and signal groups can enumerate a "
                "member set")
        if rule.audience == "room" and not discovery.capabilities.room_audience:
            raise StartupError(
                f"onboarding collection {rule.name!r}: a {rule.platform} "
                "collection is never a room's audience — a guild's member "
                "set is not a channel's; use audience=\"collection\" with "
                "a unit or workspace scope")
        service = cfg.identity.provisioning[rule.service]
        rules.append(OnboardingRule(
            name=rule.name,
            actor=Actor(ActorKind.SERVICE, service_id=rule.service,
                        sources=frozenset(service.sources)),
            platform=rule.platform,
            collection=rule.collection,
            scope=Scope(ScopeKind(rule.scope.kind), rule.scope.id),
            discovery=discovery))
    return OnboardingWiring(onboarding=PlatformOnboarding(
        rules=tuple(rules),
        provisioning=RosterProvisioning(
            services.bindings, services.registry, services.topology,
            services.registration, services.authority),
        registration=services.registration,
        bindings=services.bindings,
        authority=services.authority,
        interval_seconds=config.interval_seconds,
        retry_seconds=config.retry_seconds,
        freshness=services.freshness))


def _discovery_for(
    platform: str, *, cfg: Any, discord_control: Any, discord_token: str
) -> CollectionDiscovery | None:
    """The platform's discovery over this deployment's transport; None when it cannot enumerate."""
    if platform == "discord":
        if cfg.platforms.discord.mode == "bridge":
            if discord_control is None:
                return None

            async def bridge_fetch(
                guild_id: str
            ) -> ObservedRows | None:
                data = await discord_control.read(
                    "members", {"guild_id": guild_id})
                if data is None:
                    return None
                rows = (data.payload or {}).get("members")
                if not isinstance(rows, list):
                    return None
                # The bridge read live when asked; its own stamp is the
                # observation time — an envelope without one stays undated.
                return ObservedRows(rows=tuple(rows), observed_ms=data.observed_ms)

            return DiscordDiscovery(fetch=bridge_fetch)
        if not discord_token:
            return None
        roster = DiscordRoster(discord_token)

        async def direct_fetch(collection: str) -> ObservedRows | None:
            rows = await roster.guild_members(collection)
            if rows is None:
                return None
            # The direct read IS the upstream observation — its own
            # completion instant is the honest observed time.
            return ObservedRows(
                rows=tuple(rows), observed_ms=int(time.time() * 1000))

        return DiscordDiscovery(fetch=direct_fetch)
    if platform == "signal":
        account = cfg.platforms.signal.account
        if cfg.platforms.signal.mode == "bridge":
            endpoint = BridgeEndpoint(
                cfg.platforms.signal.base_url,
                cfg.credentials.get(cfg.bridges.token_env, ""))
            return SignalDiscovery(
                fetch=_signal_bridge_fetch(endpoint), own_account=account)
        base_url = cfg.platforms.signal.base_url
        if not base_url:
            return None

        async def direct_fetch(collection: str) -> ObservedRows | None:
            try:
                groups = await directory.group_members(
                    base_url=base_url, account=account, timeout=30.0)
            except directory.DirectoryError:
                return None
            rows = groups.get(collection)
            if rows is None:
                return None
            return ObservedRows(
                rows=rows, observed_ms=int(time.time() * 1000))

        return SignalDiscovery(fetch=direct_fetch, own_account=account)
    return None


def _signal_bridge_fetch(
    endpoint: BridgeEndpoint,
) -> Callable[[str], Awaitable[ObservedRows | None]]:
    """The Signal bridge roster and its producer timestamp."""

    async def fetch(collection: str) -> ObservedRows | None:
        data = await bridge_query(endpoint, "roster", {"conversation_id": collection})
        members = (data.payload or {}).get("members") if data is not None else None
        if data is None or not isinstance(members, list):
            return None
        return ObservedRows(
            rows=tuple(m for m in members if isinstance(m, dict)),
            observed_ms=data.observed_ms)

    return fetch
