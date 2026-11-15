"""composition step — `[identity.onboarding]` → `PlatformOnboarding`.

Rules are built ONLY when the managed backend is selected AND the operator
declared a collection; both conditions failing leaves `None` and the
runtime spawns nothing (the default file mode and empty config keep
today's behavior exactly — see `config_identity.OnboardingConfig`).

Each rule's discovery is platform-shaped: Discord gets the guild member
read (direct REST or the bridge's `/v1/reads` mirror), Signal the
`listGroups` member rows (direct RPC or the bridge's roster op), and a
platform that cannot enumerate a member set — Telegram's OIDC identity,
WhatsApp — refuses to BUILD rather than fabricate a roster. A rule that
survives to `OnboardingRule` inherits the provisioning service's own
actor, so every roster the runner pushes is admitted under the same
credential+scope check the private endpoint applies.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import aiohttp

from nora.domain.managed_ports import CollectionDiscovery
from nora.domain.management import Actor, ActorKind, Scope, ScopeKind
from nora.people import OnboardingRule, PlatformOnboarding, RosterProvisioning
from nora.platform.bridge.client import BridgeEndpoint
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
        retry_seconds=config.retry_seconds))


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
            ) -> list[dict[str, Any]] | None:
                data = await discord_control.read(
                    "members", {"guild_id": guild_id})
                if data is None or not data.get("available"):
                    return None
                rows = (data.get("value") or {}).get("members")
                return rows if isinstance(rows, list) else None

            return DiscordDiscovery(fetch=bridge_fetch)
        if not discord_token:
            return None
        return DiscordDiscovery(fetch=DiscordRoster(discord_token).guild_members)
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

        async def direct_fetch(
            collection: str
        ) -> tuple[dict[str, Any], ...] | None:
            try:
                groups = await directory.group_members(
                    base_url=base_url, account=account, timeout=30.0)
            except directory.DirectoryError:
                return None
            return groups.get(collection)

        return SignalDiscovery(fetch=direct_fetch, own_account=account)
    return None


def _signal_bridge_fetch(
    endpoint: BridgeEndpoint,
) -> Callable[[str], Awaitable[tuple[dict[str, Any], ...] | None]]:
    """`POST /v1/reads {op: "roster"}` → raw member rows; `None` on transport failure."""
    timeout = aiohttp.ClientTimeout(total=30.0)

    async def fetch(collection: str) -> tuple[dict[str, Any], ...] | None:
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session, session.post(
                f"{endpoint.base_url}/v1/reads",
                json={"op": "roster",
                      "params": {"conversation_id": collection}},
                headers={
                    "Authorization": f"Bearer {endpoint.token}"},
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
        except (TimeoutError, aiohttp.ClientError, ValueError):
            return None
        if not isinstance(data, dict) or not data.get("available"):
            return None
        members = data.get("members")
        if not isinstance(members, list):
            return None
        return tuple(m for m in members if isinstance(m, dict))

    return fetch
