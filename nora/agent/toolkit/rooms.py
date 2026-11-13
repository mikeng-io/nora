from __future__ import annotations

from collections.abc import Mapping, Sequence

from nora.agent.lineage import entry_permits
from nora.agent.room import RoomView, _key, _names_of, tell_apart
from nora.agent.security import scan_identifiers
from nora.domain.lineage import (
    RenderRoom,
    RoomFacts,
    room_key,
)
from nora.domain.lineage import message as message_lineage
from nora.domain.models import (
    PersonRef,
    TranscriptRow,
)
from nora.domain.self_presence import KnownRoom, platform_name

ROOM_ACTIVITY_WINDOW_HOURS = 24

_ROOM_CONNECTION: Mapping[str, str] = {
    "up": "up",
    "reconnecting": "reconnecting",
    "needs_a_person": "needs a person",
    "not_running": "not running",
    "unknown": "could not check",
}

_UNCHECKED = "could not check"


def _room_name(room: KnownRoom) -> str:
    """What a room is called to her: the operator's label, or its network when"""
    return room.label or platform_name(room.platform)


def _fold(text: str) -> str:
    return " ".join(str(text).split()).casefold()


def _find_room(rooms: Sequence[KnownRoom], asked: str) -> KnownRoom | None:
    """The one room of THIS unit `asked` names, or `None`."""
    key = _fold(asked)
    if not key:
        return None
    named = [r for r in rooms if _fold(_room_name(r)) == key]
    if named:
        return named[0] if len(named) == 1 else None
    on_network = [r for r in rooms if key in {_fold(r.platform), _fold(platform_name(r.platform))}]
    return on_network[0] if len(on_network) == 1 else None


_ACTIVITY_KEY = f"messages_last_{ROOM_ACTIVITY_WINDOW_HOURS}h"

_SPEAKERS_KEY = f"speakers_last_{ROOM_ACTIVITY_WINDOW_HOURS}h"


def _by_activity(entry: Mapping[str, object]) -> tuple[int, int]:
    """Sort key for `room_list`: most messages first, unchecked rooms last."""
    count = entry[_ACTIVITY_KEY]
    return (0, -count) if isinstance(count, int) else (1, 0)


def _leaders(listed: Sequence[Mapping[str, object]], key: str) -> tuple[list[str], int]:
    """The rooms with the highest count under `key`, quoted, and that count."""
    counted: list[tuple[str, int]] = []
    for e in listed:
        value = e[key]
        if isinstance(value, int):
            counted.append((str(e["room"]), value))
    top = max((c for _, c in counted), default=0)
    return [f'"{name}"' for name, c in counted if c == top], top


def _joined(names: Sequence[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _busiest_sentence(listed: Sequence[Mapping[str, object]]) -> str:
    """The activity comparison `room_list` states in its `note`, so she does"""
    window = f"the last {ROOM_ACTIVITY_WINDOW_HOURS} hours"
    unchecked = [str(e["room"]) for e in listed if not isinstance(e[_ACTIVITY_KEY], int)]
    if len(unchecked) == len(listed):
        return "Activity could not be checked for any room."
    by_messages, messages = _leaders(listed, _ACTIVITY_KEY)
    by_people, people = _leaders(listed, _SPEAKERS_KEY)
    if messages == 0:
        # R-27: an absence is claimed only over rooms that were READ. With a
        # room unchecked, «no room had messages» would cover it too.
        sentence = (
            f"The rooms that could be checked had no messages in {window}."
            if unchecked
            else f"No room of this group had messages in {window}."
        )
    else:
        each_m = " each" if len(by_messages) > 1 else ""
        each_p = " each" if len(by_people) > 1 else ""
        m_noun = "message" if messages == 1 else "messages"
        p_noun = "person" if people == 1 else "people"
        if by_messages == by_people:
            verb = "was" if len(by_messages) == 1 else "were"
            sentence = (
                f"{_joined(by_messages)} {verb} the busiest in {window}: "
                f"{messages} {m_noun} from {people} {p_noun}{each_m}."
            )
        else:
            sentence = (
                f"{_joined(by_messages)} had the most messages in {window} "
                f"({messages}{each_m}), and {_joined(by_people)} had the most "
                f"people talking ({people}{each_p})."
            )
    if unchecked:
        sentence += (
            " Activity could not be checked for "
            + ", ".join(f'"{name}"' for name in unchecked)
            + "."
        )
    return sentence


_AMBIGUOUS_PERSON = (
    "More than one person here goes by that. Ask which one, or call again with one of could_be."
)

_AMBIGUOUS_ASK = "More than one person here goes by that. Ask which one."


def _ambiguous_person(
    matches: Sequence[PersonRef],
    among: Sequence[PersonRef],
    *,
    by_id: bool,
) -> dict:
    """`ambiguous`, with `could_be` only when every candidate has something"""
    labels = tell_apart(matches, among, by_id=by_id)
    if any(label is None for label in labels):
        return {"status": "ambiguous", "note": _AMBIGUOUS_ASK}
    return {"status": "ambiguous", "could_be": labels, "note": _AMBIGUOUS_PERSON}


def _readable_rooms(
    target: RenderRoom,
    here: RoomFacts,
    *,
    all_rooms: bool,
) -> tuple[list[RoomFacts], int]:
    """The rooms `record_query` may read for `here`, and how many of the"""
    if not all_rooms:
        return [here], 0
    rooms: list[RoomFacts] = []
    withheld = 0
    for other in target.rooms:
        if other.unit != here.unit:
            continue
        if other == here:
            rooms.append(other)
            continue
        entry = message_lineage(room_key(other.platform, sorted(other.ids)[0]))
        if entry_permits(entry, target, origin=None):
            rooms.append(other)
        else:
            withheld += 1
    return rooms, withheld


def _asked_about(name: str, *, content_checks: bool = True) -> dict:
    """Echo the model's own word back, unless the word is an identifier."""
    return {} if scan_identifiers(name, content_checks=content_checks) else {"asked_about": name}


def _miss(name: str, view: RoomView, *, content_checks: bool = True) -> dict:
    """The answer when a name resolves to nobody in this room."""
    if view.degraded:
        return {
            "status": "unavailable",
            **_asked_about(name, content_checks=content_checks),
            "note": "I can't check who is in this group right now, so I can't tell "
            "you who that is. Say so rather than guessing.",
        }
    return {
        "status": "not_found",
        **_asked_about(name, content_checks=content_checks),
        "note": "Nobody in this conversation goes by that. Say you're not sure who "
        "they mean. Do not guess, and do not answer as if you knew them.",
    }


def _about_ok_note(*, identity: bool, profile: bool) -> str:
    """How to read an `about` answer (ADR 0015 §3), one clause per part that"""
    parts = []
    if identity:
        parts.append("`identity` is who they are, and it stays true.")
    if profile:
        parts.append(
            "`profile` is what you concluded from what happened with them, not "
            "what they told you, so it can be wrong."
        )
    return " ".join(parts)


def _speaker(row: TranscriptRow, display_name: str = "") -> str:
    """The one name a transcript row is called by, for both tools that render one."""
    if row.is_nora:
        return display_name or row.sender_name or "you"
    return row.person_name or row.sender_name or "unknown"


def _said(body: str | None) -> str:
    """A row's body, whitespace-collapsed, for matching against the model's words."""
    return " ".join((body or "").split()).casefold()


def _known_names(persons) -> frozenset[str]:
    """Every name and alias Nora can put to a person, normalised, ≥2 chars."""
    names: set[str] = set()
    for ref in getattr(persons, "by_person", {}).values():
        names |= {key for key in _names_of(ref) if len(key) >= 2}
    return frozenset(names)


def _count_known_names(text: str, names: frozenset[str]) -> int:
    """How many distinct known person-names/aliases appear in `text`."""
    key = _key(text)
    return sum(1 for name in names if name in key)
