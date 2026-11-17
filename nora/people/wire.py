from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aiohttp import ClientSession
    from sqlalchemy.ext.asyncio import AsyncEngine

    from nora.config_identity import ManagedOryConfig
    from nora.domain.managed import ObservationFreshness
    from nora.domain.managed_ports import LinkChallengeDelivery
    from nora.people.app.holder_link import HolderLinkService
    from nora.people.app.managed import ManagedServices
    from nora.policy import Policy

from nora.agent.lineage import LineagePolicy
from nora.agent.whereis import circles_from_persons
from nora.domain.live import LiveMapping, LiveSequence
from nora.domain.ports import SteamPresenceProvider
from nora.domain.presence_circles import CircleObservations
from nora.people.adapters.tools import build as _build

"""Compose this area's tool adapters from shared dependencies."""


def room_management(services, *, rosters=None):
    """The in-room management invocation (`!!!member`, `member_update`)
    over ONE services graph — file services answer `denied` inside
    `invoke`, which is why composition always builds this rather than
    branching on the backend."""
    from nora.people.app.room_invocation import RoomManagement

    return RoomManagement(services, rosters=dict(rosters or {}))


def tool_specs(deps):
    return _build(deps)


log = logging.getLogger("nora.app.composition")


@dataclass(frozen=True)
class NoteMapsWiring:
    pass


def note_maps(allowlist) -> NoteMapsWiring:
    # The join from a note to its room's account (DEC-207): EVERY id a room is known by —
    # `Note.established_in` holds whichever one the note was born under — within the note's
    # UNIT, since ids are unique per platform and a note carries no platform.
    note_rooms: Mapping[tuple[str, str], tuple[str, str | None]] = LiveMapping(
        lambda: {
            (cid, conv.unit): (conv.primary_id, conv.label or None)
            for conv in allowlist.rooms()
            if conv.primary_id and conv.unit
            for cid in conv.ids
        }
    )

    def note_room_for(cid: str, unit: str) -> tuple[str, str | None] | None:
        return note_rooms.get((cid, unit))

    # THE SAME JOIN, for the name resolver: every id a room is known by, within the note's
    # unit, to the room's platform — what `Persons.name_transcript` needs and
    # `Note.established_in` alone cannot supply, since a note carries no platform of its…
    note_platforms: Mapping[tuple[str, str], str] = LiveMapping(
        lambda: {
            (cid, conv.unit): conv.platform
            for conv in allowlist.rooms()
            if conv.primary_id and conv.unit
            for cid in conv.ids
        }
    )

    def note_platform_for(cid: str, unit: str) -> str | None:
        return note_platforms.get((cid, unit))

    return NoteMapsWiring()


@dataclass(frozen=True)
class UnitRoomsWiring:
    unit_rooms: Any


def unit_rooms(allowlist) -> UnitRoomsWiring:
    # The one Standing view (design/notebook-memory-types/20-standing.md): ONE reader over
    # the canonical stores, held by the tag assembler, the fire path and the cognition
    # session — a second copy per path would be the second store the design rules out.…
    unit_rooms = LiveMapping(
        lambda: {
            unit: [(c.platform, c.primary_id) for c in allowlist.rooms() if c.unit == unit]
            for unit in {c.unit for c in allowlist.rooms() if c.unit}
        }
    )
    return UnitRoomsWiring(unit_rooms=unit_rooms)


@dataclass(frozen=True)
class UnitScopesWiring:
    unit_scopes: Any


def unit_scopes(allowlist) -> UnitScopesWiring:
    # The same closure over the OTHER key. Sorted and de-duplicated: two rooms of one unit
    # are one unit of work, and a set would reorder the tick between runs of the same build
    # — which turns "did adaptor X run for unit Y this tick" into a question you cannot…
    unit_scopes: Callable[[], Sequence[str]] = lambda: tuple(  # noqa: E731
        sorted(
            {conv.unit for conv in allowlist.rooms() if conv.unit}
        )
    )
    return UnitScopesWiring(unit_scopes=unit_scopes)


@dataclass(frozen=True)
class HomesWiring:
    proactive_homes: Any


def homes(allowlist) -> HomesWiring:
    # The participation occasion's runner — a SECOND `ModelSource`, the shape the Dreamer
    # below uses, and for the same reason: `tool_choice` lives on `ModelSpec`. The
    # determination must be able to end in SILENCE, and the tag runner's `FIRST_CALL`…
    proactive_homes = LiveMapping(lambda: allowlist.home_rooms())
    return HomesWiring(proactive_homes=proactive_homes)


@dataclass(frozen=True)
class SteamWiring:
    steam_client: Any


def steam(cfg) -> SteamWiring:
    # `online_now`'s Steam half is wired ONLY when `[online] enabled` AND the key the
    # section NAMES is present — the enabled-AND-keyed gate `search` uses. Off, or keyless,
    # leaves the registry without a provider and the `online`/`playing` arms answer…
    steam_client: SteamPresenceProvider | None = None
    if cfg.online.enabled:
        steam_key = cfg.credentials.get(cfg.online.api_key_env, "")
        if steam_key:
            from nora.infra.steam import SteamPresenceClient

            steam_client = SteamPresenceClient(
                api_key=steam_key,
                timeout_seconds=cfg.online.timeout_seconds,
            )
        else:
            log.warning(
                "online enabled but the variable [online] api_key_env names is unset — "
                "online_now's Steam arms stay unavailable",
            )
    return SteamWiring(steam_client=steam_client)


@dataclass(frozen=True)
class LineageWiring:
    lineage: Any


def lineage(allowlist, cfg, persons) -> LineageWiring:
    # LINEAGE: the ONE value that says who each room of the policy is, held by the fire path
    # below and by `record_query` through the registry. The tag path's assembler builds the
    # same value from the same three inputs (`build_assembler`).
    lineage = LineagePolicy.from_policy(
        allowlist, circles=LiveSequence(lambda: circles_from_persons(persons)),
        mode=cfg.lineage.mode, content_checks=cfg.content_checks.enabled)
    return LineageWiring(lineage=lineage)


# ---------------------------------------------------------------------------
# Managed identity (`design/managed-identity-access/`): reached only through `nora.people`.
# ---------------------------------------------------------------------------


def file_services(persons, policy):
    """The `file` backend's services over the loaded Persons/Policy —
    reads answer, every managed mutator refuses (`denied`)."""
    from nora.access import IdentityAccess
    from nora.people.adapters.file_backend import (
        FileAuthorityBackend,
        FileBindingStore,
        FileIdentityRegistry,
        FileLinkRequests,
        FileTopologySource,
    )
    from nora.people.app.managed import ManagedServices

    return ManagedServices(
        bindings=FileBindingStore(persons),
        backend=FileAuthorityBackend(policy),
        topology=FileTopologySource(policy),
        registry=FileIdentityRegistry(),
        links=FileLinkRequests(),
        # File mode IS the selected backend, so the read surface keeps the
        # loaded snapshots for the descriptive reads only file data holds.
        access=IdentityAccess.for_files(persons, policy),
    )


def managed_services(
    *, managed: ManagedOryConfig, session: ClientSession, policy: Policy,
    engine: AsyncEngine, credentials: Mapping[str, str],
    freshness: ObservationFreshness | None = None,
    circle_observations: CircleObservations | None = None,
) -> ManagedServices:
    """The `managed` backend's services: PostgreSQL bindings, the Keto
    authority engine, Kratos identity records — topology still read from the
    loaded `Policy`, because topology stays Nora configuration in every
    backend (`authorization.md`).

    `managed` is a validated `ManagedOryConfig`; `session` the shared
    aiohttp session the runtime owns. `credentials` is Config.credentials:
    private secret values with explicit environment overrides.
    """
    from nora.infra.ory import KetoClient, KratosClient
    from nora.infra.sql.account_links import SqlLinkRequests
    from nora.infra.sql.managed_identity import SqlBindingStore
    from nora.people.adapters.file_backend import FileTopologySource
    from nora.people.adapters.keto_authority import KetoAuthorityBackend
    from nora.people.adapters.kratos_registry import KratosIdentityRegistry
    from nora.people.app.managed import ManagedServices

    def key(name):
        if not name:
            return None
        value = credentials.get(name)
        if not value:
            raise ValueError("configured Ory credential is unavailable")
        return value

    keto_client = KetoClient(
        session=session,
        read_url=managed.keto_read_url,
        write_url=managed.keto_write_url,
        timeout_seconds=managed.timeout_seconds,
        api_key=key(managed.keto_key_env),
    )
    kratos_client = KratosClient(
        session=session,
        admin_url=managed.kratos_admin_url,
        timeout_seconds=managed.timeout_seconds,
        api_key=key(managed.kratos_admin_key_env),
    )
    return ManagedServices(
        bindings=SqlBindingStore(engine=engine),
        backend=KetoAuthorityBackend(keto_client),
        topology=FileTopologySource(policy),
        registry=KratosIdentityRegistry(kratos_client),
        links=SqlLinkRequests(engine=engine),
        freshness=freshness, circle_observations=circle_observations,
    )


def holder_links(
    services: ManagedServices,
    *,
    signal_delivery: LinkChallengeDelivery | None = None,
) -> HolderLinkService:
    """The holder-facing link lifecycle (`account-linking.md`) over one
    `ManagedServices` — request, private view, decision and holder-exact
    unlink. `signal_delivery` (a `LinkChallengeDelivery`) is injected per
    deployment: absent, no challenge is pushed and the holder's
    authenticated view still carries their own side."""
    from nora.people.app.holder_link import HolderLinkService

    return HolderLinkService(
        services.bindings,
        services.registry,
        services.links,
        services.publication,
        services.registration,
        signal_delivery=signal_delivery,
    )
