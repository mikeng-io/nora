"""What `character_read` resolves before it reads: which accounts of THIS unit a
model's `room` or `person` argument names (design/self-growth/10-character.md
§6 C4, design/cognition-loop/50-character-and-standing.md §5).

**The unit is the boundary, drawn by scope.** Every function here is handed the
unit's own rooms and nothing else, so a room or a person of another unit is not
refused after being found: it was never a candidate, and lands on the same
`None` as a name nobody uses. A person resolves only through the rooms that
list them: a `persons = "*"` room lists nobody (`Persons.room_members`'s rule),
so it is the fail-closed side.

**No lineage filter: the unit is the read boundary** (DEC-231; `[lineage]` is
do-not-enable, design/cognition-loop/10-tiers.md §3, AGENTS.md). Text read
here can reach another room of the same unit through a `remind` brief, which
the unit boundary permits. Accounts are linked, never cited, so anything
built from one carries no lineage of its own.
`tests/unit/test_tool_surface.py::_LOOP_ONLY` pins it off the tag and fire
paths. Account bodies are screened for identifiers on the way out (R-18).

**`account_key` is the ONE place a store key is built.** Accounts are keyed per
room today; the per-unit key for a person (C2b) changes this function and no
caller.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from nora.domain.culture import character_key
from nora.domain.lineage import RoomFacts
from nora.domain.models import PersonRef

__all__ = ["account_key", "person_named", "room_named", "rooms_listing"]


def account_key(room: RoomFacts, person_id: str | None = None) -> str | None:
    """The `culture` key of `room`'s account, or of `person_id`'s account in it.
    `None` for a room built without a `primary_id`: nothing was written there."""
    if not room.primary_id:
        return None
    if person_id is None:
        return room.primary_id
    return character_key(room.primary_id, person_id)


def _fold(text: str) -> str:
    return " ".join(str(text).split()).casefold()


def room_named(rooms: Sequence[RoomFacts], asked: str) -> RoomFacts | None:
    """The one room of this unit whose label is `asked` (case and spacing
    folded), or `None`. Two matches are `None`: a guess between two rooms is a
    read of the wrong one."""
    wanted = _fold(asked)
    if not wanted:
        return None
    found = [r for r in rooms if r.label and _fold(r.label) == wanted]
    return found[0] if len(found) == 1 else None


def person_named(by_person: Mapping[str, PersonRef], asked: str) -> str | None:
    """The `person_id` `asked` names, as an id or as a name, or `None` when it
    names nobody or more than one person."""
    wanted = _fold(asked)
    if not wanted:
        return None
    if asked.strip() in by_person:
        return asked.strip()
    found = [pid for pid, ref in by_person.items() if _fold(ref.name) == wanted]
    return found[0] if len(found) == 1 else None


def rooms_listing(rooms: Sequence[RoomFacts], person_id: str) -> tuple[RoomFacts, ...]:
    """The rooms of this unit whose `persons` list names `person_id`."""
    return tuple(r for r in rooms if r.persons is not None and person_id in r.persons)
