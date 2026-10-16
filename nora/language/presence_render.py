"""`<presence>` — the ONE renderer for where she is.

Called by the tag and fire paths (`nora.agent.prefill`); it lives in
`nora.language`, the layer every occasion can import. No occasion builds its
own copy, because two sources for «which network am I on» is the drift the
context-assembly registry exists to prevent.

**Situation, never ability.** Every line says where she is or what is around
her; none says what she can do. Telling a model what it is READING cut the
Relevance Gate's errors, and telling it what it CAN DO made them measurably
worse. Abilities are the tool catalogue's.
"""

from __future__ import annotations

from nora.domain.blocks import ContextBlock
from nora.domain.prompt import escape
from nora.domain.self_presence import (
    PLATFORM_NAMES,
    Presence,
    TransportState,
    platform_name,
)

#: The order networks are listed in the handle line — the table's own order,
#: then anything it does not name, alphabetically — so the same handles render
#: the same sentence every turn.
_NETWORK_ORDER = {name: i for i, name in enumerate(PLATFORM_NAMES)}

#: What a non-`up` state says. `up` has no phrase of its own: it is «the
#: others are up» when something is not, and «all up» when nothing is.
_STATE_PHRASE: dict[TransportState, str] = {
    "reconnecting": "{net} is reconnecting right now",
    "needs_a_person": "{net} is down and needs a person to fix it",
    "not_running": "{net} is not running in this deployment",
    "unknown": "{net}'s connection could not be checked",
}


def state_phrase(network: str, state: TransportState) -> str:
    """What a non-`up` `state` says about `network`, in `<presence>`'s words.

    Public so `send_to`'s refusal (`nora.agent.relay`) names a down network in
    the same sentence the block showed her, rather than a second wording of it.
    """
    return _STATE_PHRASE[state].format(net=platform_name(network))


def _listing(items: list[str]) -> str:
    """`a`, `a and b`, `a, b and c`."""
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _named(platform: str, label: str) -> str:
    return f"{platform_name(platform)} («{escape(label)}»)" if label else platform_name(platform)


def _here_line(presence: Presence) -> str:
    net = platform_name(presence.platform)
    if presence.direct:
        return f"You are in a direct conversation on {net}."
    if presence.label:
        return f"You are in «{escape(presence.label)}» on {net}."
    return f"You are on {net} right now."


def _handles_line(presence: Presence) -> str:
    """`You are @a on X and Y, and @b on Z.` — grouped by handle, so a name
    she uses on four networks is said once."""
    ordered = sorted(
        presence.handles,
        key=lambda pair: (_NETWORK_ORDER.get(pair[0], len(_NETWORK_ORDER)), pair[0]),
    )
    groups: dict[str, list[str]] = {}
    for network, handle in ordered:
        groups.setdefault(handle, []).append(platform_name(network))
    parts = [f"{escape(handle)} on {_listing(nets)}" for handle, nets in groups.items()]
    if not parts:
        return ""
    if len(parts) == 1:
        return f"You are {parts[0]}."
    return "You are " + ", ".join(parts[:-1]) + ", and " + parts[-1] + "."


def _rooms_line(presence: Presence) -> str:
    if presence.unit_rooms is None or presence.direct:
        return ""
    if not presence.unit_rooms:
        return "This group has no rooms anywhere else."
    named = [_named(room.platform, room.label) for room in presence.unit_rooms]
    return f"This group also has rooms on {_listing(named)}."


def _transport_line(presence: Presence) -> str:
    """Which of this group's networks are up. One state per network: two rooms
    on one network share one adapter, so they cannot differ."""
    states: dict[str, TransportState] = {}
    if presence.here_state is not None:
        states[presence.platform] = presence.here_state
    for room in presence.unit_rooms or ():
        states.setdefault(room.platform, room.state)
    if not states:
        return ""
    down = [
        _STATE_PHRASE[state].format(net=platform_name(net))
        for net, state in states.items() if state != "up"
    ]
    if not down:
        return "Every one of them is up right now." if len(states) > 1 else (
            f"{platform_name(next(iter(states)))} is up right now."
        )
    if len(down) == len(states):
        return "; ".join(down) + "."
    return "; ".join(down) + "; the others are up."


def _others_line(presence: Presence) -> str:
    """D1 = B: that other groups exist, never which."""
    if not presence.other_groups:
        return ""
    if presence.direct:
        return "You are also in group chats, which you do not discuss here."
    return "You are also in other groups, which you do not discuss here."


def render_presence(presence: Presence, *, room_is: str | None = None) -> ContextBlock:
    """`<presence>` for one turn. Always a block: the here line cannot be
    empty, and a turn with no situational model is the bug this replaces.

    `room_is` is the operator's own description of the room she is in
    (`policy.Conversation.description`), carried as the `this_room_is`
    attribute. Absent renders no attribute.
    """
    lines = [
        _here_line(presence),
        _handles_line(presence),
        _rooms_line(presence),
        _transport_line(presence),
        _others_line(presence),
    ]
    attrs: tuple[tuple[str, str], ...] = (
        (("this_room_is", room_is),) if room_is else ()
    )
    return ContextBlock(
        name="presence", attrs=attrs, body="\n".join(line for line in lines if line),
    )
