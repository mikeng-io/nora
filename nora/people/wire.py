import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from nora.app.assembler import lineage_policy
from nora.domain.live import LiveMapping
from nora.domain.ports import SteamPresenceProvider
from nora.people.adapters.tools import build as _build
from nora.web.games import PULSE_API_BASE, PulseGamesClient

"""Compose this area's tool adapters from shared dependencies."""


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
                "online enabled but %s is unset — online_now's Steam arms stay unavailable",
                cfg.online.api_key_env,
            )
    return SteamWiring(steam_client=steam_client)


@dataclass(frozen=True)
class GamesWiring:
    games_client: Any


def games(cfg) -> GamesWiring:
    games_client = None
    if pulse_api_token := cfg.credentials.get("PULSE_API_TOKEN"):
        # The base URL names a private tailnet host, so the deployment states it
        # in `.env` (`PULSE_API_BASE`); the source holds only a placeholder.
        games_client = PulseGamesClient(
            token=pulse_api_token,
            base_url=cfg.credentials.get("PULSE_API_BASE") or PULSE_API_BASE,
        )
    return GamesWiring(games_client=games_client)


@dataclass(frozen=True)
class LineageWiring:
    lineage: Any


def lineage(allowlist, cfg, persons) -> LineageWiring:
    # LINEAGE: the ONE value that says who each room of the policy is, held by the fire path
    # below and by `record_query` through the registry. The tag path's assembler builds the
    # same value from the same three inputs (`build_assembler`).
    lineage = lineage_policy(cfg, policy=allowlist, persons=persons)
    return LineageWiring(lineage=lineage)
