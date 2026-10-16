"""Where Nora is right now, as data.

Named `self_presence` rather than `presence` because `nora.presence` is the
Life360 location service — where the PEOPLE are. This module is where SHE is:
which room a turn is in, which other rooms of the same group exist, whether
each network's transport is up, and the public handles people use for her.

Pure data and one pure function. Reading policy and adapter health is
`nora.engagement.self_presence.PresenceReader`'s job; rendering it is
`nora.language.presence_render`'s. Nothing here does I/O, so the same model
reaches the tag path and the fire path unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    # Type-only: `ports` imports `models`, which imports this module for
    # `TagContext.presence`. A runtime import here would close that cycle.
    from .ports import AdapterHealth

#: How each network is named to her. A display name, not the wire key:
#: `signal` is a value on the wire, `Signal` is what the room calls it.
#:
#: Lives here, not in `nora.agent.prefill`, because passes in `nora.language`
#: render the same block and may not import `nora.agent`. `x` and `threads`
#: have no adapter; they are here for the handle line, which names every
#: network she has a public handle on.
#:
#: A network with no entry falls back to its key rather than being hidden:
#: «I am somewhere I have no name for» is a better situational model than
#: silence, which would let a Discord tag be answered as if on Signal.
PLATFORM_NAMES: dict[str, str] = {
    "signal": "Signal",
    "discord": "Discord",
    "whatsapp": "WhatsApp",
    "instagram": "Instagram",
    "x": "X",
    "threads": "Threads",
}


def platform_name(platform: str) -> str:
    """The display name for `platform`, or the key itself when it has none."""
    return PLATFORM_NAMES.get(platform, platform)


#: What one network's transport is doing right now.
#:
#: * `up` — connected.
#: * `reconnecting` — not connected, and expected to heal on its own.
#: * `needs_a_person` — `AdapterHealth.degraded`: present, registered, and
#:   unable to act until a HUMAN intervenes (an Instagram checkpoint). This
#:   wins over `connected`, because a checkpointed session can still hold a
#:   socket open and refuse every send.
#: * `not_running` — no adapter for that network in this process at all.
#: * `unknown` — `health()` raised. R-27's `unavailable`: «I could not check»,
#:   never rendered as `up`.
TransportState = Literal["up", "reconnecting", "needs_a_person", "not_running", "unknown"]


def transport_state(health: AdapterHealth) -> TransportState:
    """One adapter's health report, reduced to what she needs to know.

    `detail` is deliberately NOT read. It is the operator's diagnostic («instagram
    session is 'checkpointed'»), and what she needs is whether a message there
    goes through, not the bridge's vocabulary for why not.
    """
    if health.degraded:
        return "needs_a_person"
    return "up" if health.connected else "reconnecting"


@dataclass(frozen=True)
class PresenceRoom:
    """One OTHER room of the group she is in.

    `label` is the operator's `policy.toml` label, or `""` when there is none.
    It is never an id: R-18 keeps identifiers off the wire, so a room with no
    label is named by its network alone.
    """

    platform: str
    label: str
    state: TransportState


@dataclass(frozen=True)
class Presence:
    """Where she is on this turn, generated rather than remembered.

    `unit_rooms` is `None` when the room could not be found in policy, which is
    a different state from `()` («this group has no other rooms»): the first is
    «I did not look», and it renders nothing rather than a claim.

    `other_groups` says only whether she is in any room OUTSIDE this group.
    Which ones is never carried here at all, so no renderer can leak
    what the model never received.

    `handles` is `((network, handle), ...)`: display text for the names people
    use to reach her. Never an `Identifier`, never matched against anything
    (`nora.domain.identity`'s no-username rule is about recognising other
    people; this is what she is called).
    """

    platform: str
    label: str = ""
    direct: bool = False
    here_state: TransportState | None = None
    unit_rooms: tuple[PresenceRoom, ...] | None = None
    other_groups: bool = False
    handles: tuple[tuple[str, str], ...] = ()

    @classmethod
    def minimal(cls, platform: str, *, direct: bool = False) -> Presence:
        """The network alone, for a context nobody read a presence for.

        The network and nothing more: no room label, no other rooms, no
        health. A harness with no reader still tells her
        which network she is on, and claims nothing it did not read.
        """
        return cls(platform=platform, direct=direct)


@dataclass(frozen=True)
class KnownRoom:
    """One room of the unit a turn is in, as the room tools read it.

    `conversation_id` is here so a tool can read that room's stored messages.
    It is an identifier and is NEVER put in a tool result (R-18): the tool
    names a room by `label`, or by its network when it has none.

    `members` counts the accounts the platform lists, Nora's own excluded, or
    is `None` when the roster could not be read. `None` is «I could not
    check», never 0.
    """

    platform: str
    label: str
    conversation_id: str
    here: bool
    state: TransportState
    members: int | None = None


@dataclass(frozen=True)
class UnitRooms:
    """Every room of this turn's unit, and whether any other unit exists.

    `rooms` is `None` when the turn's room is not in policy (or is a direct
    chat): there is no unit to list, which is a different state from a unit
    with one room. `other_groups` says only THAT she is in rooms outside this
    unit; which ones is never carried, so no tool can leak them.
    """

    rooms: tuple[KnownRoom, ...] | None
    direct: bool = False
    other_groups: bool = False
