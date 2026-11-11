from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any, cast

from openai.types.shared.reasoning_effort import ReasoningEffort

from nora.agent.dreamer import AdaptorBudget
from nora.agent.room import Room
from nora.app.startup import StartupError
from nora.config import Config, DreamBudgetConfig
from nora.domain.culture import character_key, unit_culture_key
from nora.domain.ports import RosterSource
from nora.engagement.self_presence import PresenceReader
from nora.language.character import (
    CHARACTER_NORA_ADAPTOR_NAME,
    CHARACTER_PERSON_ADAPTOR_NAME,
    CHARACTER_UNIT_ADAPTOR_NAME,
    CharacterRoom,
    PersonKey,
    UnitKey,
)
from nora.language.profile import PROFILE_PERSON_ADAPTOR_NAME
from nora.language.profile_collective import (
    PROFILE_NORA_ADAPTOR_NAME,
    PROFILE_ROOM_ADAPTOR_NAME,
    PROFILE_UNIT_ADAPTOR_NAME,
    CollectiveKey,
)
from nora.persons import Persons
from nora.platform.discord.adapter import DiscordAdapter
from nora.platform.discord.bridge_control import BridgeDiscordAdapter
from nora.platform.instagram.adapter import InstagramAdapter
from nora.platform.registry import AdapterRegistry
from nora.policy import Policy

"""Small pure helpers the composition root's steps share."""


_REASONING_EFFORTS: frozenset[str] = frozenset(
    {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
)





def _room_claims(allowlist: Policy)-> dict[str, frozenset[tuple[str, str]]]:

    """Conversation id -> every `(unit, platform)` pair policy.toml assigns it,"""
    out: dict[str, set[tuple[str, str]]] = {}
    for conv in allowlist.rooms():
        for cid in conv.ids:
            out.setdefault(cid, set()).add((conv.unit, conv.platform))
    return {cid: frozenset(owners) for cid, owners in out.items()}


def _collective_members(
    policy: Policy, keys: Mapping[str, CollectiveKey],
) -> dict[str, tuple[str, ...]]:
    return {
        k: tuple(
            sorted(
                {
                    pid
                    for r in ck.rooms
                    for convo in policy.rooms()
                    if convo.primary_id == r.key
                    for pid in convo.persons or ()
                }
            )
        )
        for k, ck in keys.items()
    }


def _presence_reader(
    cfg: Config, allowlist: Policy, registry: AdapterRegistry,
    rosters: Mapping[str, RosterSource] | None = None,
) -> PresenceReader:
    """The one `PresenceReader`."""
    live: dict[str, Callable[[], Awaitable[str | None]]] = {}
    instagram = registry.adapters.get("instagram")
    if isinstance(instagram, InstagramAdapter):
        live["instagram"] = instagram.own_handle
    discord_adapter = registry.adapters.get("discord")
    if isinstance(discord_adapter, (DiscordAdapter, BridgeDiscordAdapter)):
        live["discord"] = discord_adapter.own_handle
    return PresenceReader(
        policy=allowlist,
        adapters=dict(registry.adapters),
        handles=cfg.agent.handles,
        live_handles=live,
        # The room tools' member counts: the SAME
        # roster map `Room` reads, and her own ids so she is never counted as
        # a member of a room she is in. `<presence>` reads neither.
        rosters=rosters,
        self_ids=registry.self_aliases(),
    )


def _reasoning_effort(value: str, *, source: str)-> ReasoningEffort:

    """Narrow a config string to the provider's closed set, or refuse to start."""
    if value not in _REASONING_EFFORTS:
        raise StartupError(
            f"{source} = {value!r} is not a reasoning effort the provider "
            f"accepts ({', '.join(sorted(_REASONING_EFFORTS))}) — refusing to "
            f"start rather than failing on the first call that uses it"
        )
    return cast(ReasoningEffort, value)





def _otp_secrets(cfg: Config)-> tuple[bytes, str]:

    """The HMAC key and the API bearer token, checked at BOOT when `[otp]"""
    values = []
    for name in (cfg.otp.secret_env, cfg.otp.api_token_env):
        value = cfg.credentials.get(name, "").strip()
        if not value:
            raise StartupError(
                f"[otp] enabled = true but {name} is unset or empty (the value "
                "lives in .env, DEC-8); refusing to start"
            )
        values.append(value)
    return values[0].encode(), values[1]


def _now_ms() -> int:
    """Wall-clock epoch milliseconds, the Lessons book's and evidence's clock."""
    return int(datetime.now(UTC).timestamp() * 1000)


def character_unit_keys(allowlist: Any)-> dict[str, UnitKey]:

    """One key per unit, with its rooms in policy order."""
    rooms_of: dict[str, list[CharacterRoom]] = {}
    for conv in allowlist.rooms():
        if conv.primary_id and conv.unit and conv.absorbed:
            rooms_of.setdefault(conv.unit, []).append(
                CharacterRoom(
                    key=conv.primary_id,
                    platform=conv.platform,
                    label=conv.label,
                )
            )
    return {
        unit_culture_key(unit): UnitKey(unit=unit, rooms=tuple(rooms))
        for unit, rooms in rooms_of.items()
    }


def character_person_keys(allowlist: Any, persons: Any)-> dict[str, PersonKey]:

    """One key per (unit, person listed in any of that unit's rooms), from
    `policy.toml` and `identity.toml`. A
    person's evidence and room cultures are the unit's rooms, in policy order;
    their read tools stand in the first of those that lists them. A `*` room
    lists nobody by name and adds no person."""
    rooms_of: dict[str, list[CharacterRoom]] = {}
    listing: dict[tuple[str, str], list[str]] = {}
    refs: dict[str, Any] = {}
    for conv in allowlist.rooms():
        if not conv.primary_id or not conv.unit:
            continue
        rooms_of.setdefault(conv.unit, []).append(
            CharacterRoom(
                key=conv.primary_id,
                platform=conv.platform,
                label=conv.label,
            )
        )
        for ref in persons.room_members(conv.persons):
            refs[ref.person_id] = ref
            listing.setdefault((conv.unit, ref.person_id), []).append(conv.primary_id)
    return {
        character_key(unit, person_id): PersonKey(
            unit=unit,
            person=refs[person_id],
            rooms=tuple(rooms_of[unit]),
            home=homes[0],
            listed=tuple(homes),
        )
        for (unit, person_id), homes in listing.items()
    }


def _profile_room_keys(allowlist: Any)-> dict[str, CollectiveKey]:

    """Room `primary_id` -> the room's CollectiveKey, for every room policy"""
    return {
        conv.primary_id: CollectiveKey(
            unit=conv.unit,
            rooms=(CharacterRoom(key=conv.primary_id, platform=conv.platform, label=conv.label),),
            label=conv.label or "",
            description=conv.description or "",
        )
        for conv in allowlist.rooms()
        if conv.primary_id and conv.unit
    }


def _roster_names_of(
    rosters: Mapping[str, RosterSource], persons: Persons, self_ids: frozenset[str],
) -> Callable[[str, str], Awaitable[Mapping[str, str] | None]]:
    """A cognition session's roster seam: the room's roster NAMES with their"""

    async def roster_of(platform: str, conversation_id: str)-> Mapping[str, str] | None:

        view = await Room(
            platform=platform,
            conversation_id=conversation_id,
            roster_source=rosters.get(platform),
            persons=persons,
            self_ids=self_ids,
        ).members()
        if not view.available:
            return None
        return {p.name: p.person_id for p in view.people}

    return roster_of


def _note_unit_of(policy: Policy)-> Callable[[str, str], str | None]:

    """`(platform, conversation_id)` → the room's unit, or `None`."""

    def _resolve(platform: str, conversation_id: str)-> str | None:

        convo = policy.conversation(platform, conversation_id)
        return convo.unit if convo is not None else None

    return _resolve


def _room_ids_of(policy: Policy)-> Callable[[str, str], frozenset[str]]:

    """Every id the room `(platform, conversation_id)` answers to (`Conversation.ids`),"""

    def room_ids(platform: str, conversation_id: str)-> frozenset[str]:

        convo = policy.conversation(platform, conversation_id)
        return convo.ids if convo is not None else frozenset({conversation_id})

    return room_ids


def _music_channel_of(policy: Policy)-> Callable[[str], str | None]:

    """A Discord `guild_id` → the CHANNEL id its music announcements are"""

    def _resolve(guild_id: str)-> str | None:

        return policy.music_channel_for("discord", guild_id)

    return _resolve


CHARACTER_ADAPTOR_NAMES: tuple[str, ...] = (
    CHARACTER_PERSON_ADAPTOR_NAME,
    CHARACTER_UNIT_ADAPTOR_NAME,
    CHARACTER_NORA_ADAPTOR_NAME,
)



def _adaptor_budget(b: DreamBudgetConfig)-> AdaptorBudget:

    """`DreamBudgetConfig` -> `AdaptorBudget`: the one place the two shapes meet."""
    return AdaptorBudget(
        turns=b.turns,
        total_tokens=b.total_tokens,
        output_tokens=b.output_tokens,
        timeout_seconds=b.timeout_seconds,
        max_memory_calls=b.max_memory_calls,
        # Copied, not aliased: `DreamBudgetConfig` is frozen but its `dict`
        # field is not, and an `AdaptorBudget` handed the same object would
        # share it with the config tree for the process's life.
        tool_calls=dict(b.tool_calls),
        max_tool_calls=b.max_tool_calls,
    )


def _unified_profile_writers_retired(
    profile_index: bool, graph_comprehend: bool, cognition_enabled: bool,
    units: frozenset[str], home_units: frozenset[str],
) -> frozenset[str]:
    """The Dreamer profile writers the unified Cognition session replaces —"""
    return (
        frozenset(
            {
                PROFILE_PERSON_ADAPTOR_NAME,
                PROFILE_ROOM_ADAPTOR_NAME,
                PROFILE_UNIT_ADAPTOR_NAME,
                PROFILE_NORA_ADAPTOR_NAME,
            }
        )
        if (
            profile_index
            and graph_comprehend
            and cognition_enabled
            and units
            and units <= home_units
        )
        else frozenset()
    )
