"""The lineage predicates: may room B be shown this section? (DEC-222, DEC-225)

**Applied at assembly, never at egress** (GLOSSARY): when a pushed block
renders (`nora.agent.prefill`) and when a read tool returns
(`record_query`), for the room the turn is bound to. By the time text exists
the disclosure is decided.

One pure function per lineage kind, each answering for ONE entry and ONE room.
Room B may be shown:

- `message:<A>`: when B is A; or A and B are in one unit, B's listed persons
  are a subset of A's, neither lists `*`, and B's live roster is readable,
  names every account, and holds only listed persons of A;
- `presence` (P): when B's `location` flag is on and the live DisclosureGuard
  passes for P in B;
- `face` (P): when B is the media's origin room; or P is listed in B and B
  passes the media's own lineage;
- `social` (P), `play` (P): when P is a listed person of B;
- `perception`: when B passes the lineage of the item it reads.

Details:

- **`message`: same unit, and `*` on either side denies.** R-49 lets a message
  cross only between rooms of one unit, and DEC-225 extends R-49 to derived
  rows, so a room of another unit never passes. A `*` room in A's place has no
  roster a subset can be checked against, so nothing from it leaves it; a `*`
  room in B's place is refused.
- **`presence`: the guard is not waived by the flag.** `where_is` treats
  `location = true` as trust that relaxes the subset check (DEC-79). Here the
  flag AND the guard must both pass: B's live roster is read, a degraded or
  unreadable one denies (R-27), and B's people must be a subset of a circle
  holding P. An unmapped account in B is IGNORED, not counted: `_presence`
  never reads `live.unnamed`, so the guard runs over the named people alone
  (`live.person_ids`). That lets a flagged room pass past an unmapped account,
  but it is not DEC-79's waiver: nothing here counts it on
  `nora_presence_disclosure_waived_total`, so such a pass leaves no trace. (The
  `message` kind and `exclude_persons` are stricter and deny on `unnamed > 0`.)
  A presence entry with no subject denies: the guard is a rule about a person.
- **Unknown is deny.** An entry of a kind with no predicate, `UNRESOLVED`
  included, passes only in the row's origin room; so does a section with no
  entries. With no origin, nowhere.

`exclude_persons` is checked beside lineage for the whole row: a row naming
anyone refuses a room listing one of them, a `*` room, a room with unmapped
accounts, and a room whose live roster could not be read or holds one of them.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

from ..domain.culture import CultureRevision
from ..domain.lineage import (
    BODY,
    FACE,
    MESSAGE,
    PERCEPTION,
    PLAY,
    PRESENCE,
    ROW,
    SOCIAL,
    WORKING_SET,
    Lineage,
    LineageEntry,
    LineageMode,
    LiveRoom,
    RenderRoom,
    RoomFacts,
    room_key,
)
from ..domain.notes import Note
from ..domain.persona import PersonaLine
from ..domain.standing import Standing
from ..policy import Policy
from .room import Room
from .whereis import Circle, DisclosureGuard

__all__ = [
    "PREDICATES",
    "DerivedView",
    "LineagePolicy",
    "derived_view",
    "entry_permits",
    "excluded",
    "section_permits",
    "visible_sections",
]

# ---- the predicates, one per kind -------------------------------------------


def _message(entry: LineageEntry, target: RenderRoom) -> bool:
    b = target.room
    if b is None:
        return False
    # The room the words were said in is the entry's ORIGIN
    # (`nora.domain.lineage`, module docstring).
    if b.is_room(entry.origin):
        return True
    sources = target.resolve(entry.origin)
    if len(sources) != 1:
        # Not a room of B's unit (another unit, or no longer in the policy),
        # or ambiguous: nothing to check a subset against.
        return False
    a = sources[0]
    if a.persons is None or b.persons is None:
        return False
    if not b.persons <= a.persons:
        return False
    # Who is actually in B, not only who B lists: a listed-persons subset says
    # nothing about an account that joined B and was never listed. B's live
    # roster must be readable, whole, fully named, and every person in it a
    # listed person of A; otherwise deny.
    live = target.live
    if live is None or not live.available or live.degraded or live.unnamed > 0:
        return False
    return bool(live.person_ids) and live.person_ids <= a.persons


def _presence(entry: LineageEntry, target: RenderRoom) -> bool:
    b, live = target.room, target.live
    if b is None or not b.location or entry.subject is None:
        return False
    if live is None or not live.available or live.degraded:
        return False
    return any(
        entry.subject in members
        and DisclosureGuard(Circle(circle_id="", person_ids=members)).permits(
            live.person_ids,
        )
        for members in target.circles
    )


def _listed(entry: LineageEntry, target: RenderRoom) -> bool:
    b = target.room
    return (
        b is not None and b.persons is not None
        and entry.subject is not None and entry.subject in b.persons
    )


def _reads(entry: LineageEntry, target: RenderRoom) -> bool:
    """B passes every entry of the item this one reads; an item with no
    lineage of its own is unknown, so shown only where the entry came from."""
    if not entry.reads:
        return target.room is not None and target.room.is_room(entry.origin)
    return all(entry_permits(r, target, origin=entry.origin) for r in entry.reads)


def _face(entry: LineageEntry, target: RenderRoom) -> bool:
    b = target.room
    if b is None:
        return False
    if b.is_room(entry.origin):
        return True
    return _listed(entry, target) and _reads(entry, target)


#: kind -> predicate. A kind absent here is unknown, and `entry_permits`
#: denies it outside the row's origin room.
PREDICATES: Mapping[str, Callable[[LineageEntry, RenderRoom], bool]] = {
    MESSAGE: _message,
    PRESENCE: _presence,
    FACE: _face,
    SOCIAL: _listed,
    PLAY: _listed,
    PERCEPTION: _reads,
}


def entry_permits(entry: LineageEntry, target: RenderRoom, *, origin: str | None) -> bool:
    """May B be shown something resting on `entry`? `origin` is the room the
    row carrying it was written in: the only room an unknown kind is shown in."""
    predicate = PREDICATES.get(entry.kind)
    if predicate is None:
        return target.room is not None and target.room.is_room(origin)
    return predicate(entry, target)


def section_permits(
    entries: Iterable[LineageEntry], target: RenderRoom, *, origin: str | None,
) -> bool:
    """Every entry passes; a section with no entries is unknown, shown only in
    its origin room."""
    listed = tuple(entries)
    if not listed:
        return target.room is not None and target.room.is_room(origin)
    return all(entry_permits(e, target, origin=origin) for e in listed)


def excluded(exclude_persons: Iterable[str], target: RenderRoom) -> bool:
    """True when a row naming `exclude_persons` may NOT be shown in B: B lists
    one of them or anyone (`*`), or B's live roster holds one of them, holds
    an account Nora cannot name, or could not be read. Empty: never excluded."""
    names = frozenset(exclude_persons)
    if not names:
        return False
    b, live = target.room, target.live
    if b is None or b.persons is None or names & b.persons:
        return True
    if live is None or not live.available or live.degraded or live.unnamed > 0:
        return True
    return bool(names & live.person_ids)


def visible_sections(
    lineage: Lineage, sections: Iterable[str], target: RenderRoom, *,
    origin: str | None, exclude_persons: Iterable[str] = (),
) -> dict[str, bool]:
    """For each of `sections`, whether B may be shown it. A surprise
    (`exclude_persons`) withholds every section at once."""
    names = tuple(sections)
    if excluded(exclude_persons, target):
        return dict.fromkeys(names, False)
    return {
        name: section_permits(lineage.section(name), target, origin=origin)
        for name in names
    }


@dataclass(frozen=True)
class LineagePolicy:
    """The per-process half of an `RenderRoom`: every room of the policy and the
    location-sharing circles, fixed at startup (`policy.toml` and
    `identity.toml` are read once). `render_room` adds room B, per turn."""

    rooms: tuple[RoomFacts, ...] = ()
    circles: tuple[frozenset[str], ...] = ()
    mode: LineageMode = "off"
    _by_id: dict[tuple[str, str], RoomFacts] = field(
        default_factory=dict, repr=False, compare=False,
    )

    @classmethod
    def from_policy(
        cls, policy: Policy, *, circles: tuple[Circle, ...] = (),
        mode: LineageMode = "off",
    ) -> LineagePolicy:
        rooms = tuple(
            RoomFacts(
                platform=c.platform, ids=c.ids, unit=c.unit,
                persons=c.persons, location=c.location, label=c.label,
                primary_id=c.primary_id,
            )
            for c in policy.conversations
        )
        return cls(
            rooms=rooms, circles=tuple(c.person_ids for c in circles), mode=mode,
            _by_id={(r.platform, cid): r for r in rooms for cid in r.ids},
        )

    def render_room(
        self, *, platform: str, conversation_id: str, live: LiveRoom | None = None,
    ) -> RenderRoom:
        return RenderRoom(
            room=self._by_id.get((platform, conversation_id)),
            rooms=self.rooms, circles=self.circles, live=live, mode=self.mode,
        )


@dataclass(frozen=True)
class DerivedView:
    """What the derived blocks of one prompt may show in room B: the notes
    whose row passes, and whether each Standing and culture section does.
    `withheld` counts, per block, what did not pass (0s omitted), for the
    prefill span whatever the mode."""

    notes: tuple[Note, ...]
    standing: bool
    standing_working_set: bool
    culture: bool
    withheld: tuple[tuple[str, int], ...] = ()


def derived_view(
    *, notes: Iterable[Note], standing: Standing | None,
    culture: CultureRevision | None, target: RenderRoom,
) -> DerivedView:
    """Apply lineage to the derived rows a prompt carries: each row, field or
    section is rendered only where its lineage passes for the room. A section
    that fails is dropped whole; the rest of its row stays."""
    shown = tuple(
        n for n in notes
        if visible_sections(
            n.lineage, (ROW,), target, origin=n.established_in,
            exclude_persons=n.exclude_persons,
        )[ROW]
    )
    held = tuple(notes)
    stand = (
        visible_sections(
            standing.lineage, (BODY, WORKING_SET), target,
            origin=room_key(standing.platform, standing.conversation_id),
            exclude_persons=standing.exclude_persons,
        )
        if standing is not None else {BODY: True, WORKING_SET: True}
    )
    culture_ok = (
        visible_sections(
            culture.lineage, (BODY,), target, origin=culture.culture_key,
            exclude_persons=culture.exclude_persons,
        )[BODY]
        if culture is not None else True
    )
    counts = (
        ("notes", len(held) - len(shown)),
        ("standing", int(not stand[BODY])),
        ("standing_working_set", int(
            standing is not None and bool(standing.working_set)
            and not stand[WORKING_SET]
        )),
        ("culture", int(not culture_ok)),
    )
    return DerivedView(
        notes=shown, standing=stand[BODY],
        standing_working_set=stand[WORKING_SET], culture=culture_ok,
        withheld=tuple((name, n) for name, n in counts if n),
    )


async def persona_lines_for(
    lines: tuple[PersonaLine, ...], target: RenderRoom, room: Room,
) -> tuple[PersonaLine, ...]:
    """Apply the existing exclusion boundary, reading the roster only if needed."""
    if any(line.exclude_persons for line in lines):
        view = await room.scope()
        target = target.with_live(LiveRoom(
            person_ids=frozenset(p.person_id for p in view.people),
            unnamed=view.unnamed, available=view.available, degraded=view.degraded,
        ))
    return tuple(line for line in lines if not excluded(line.exclude_persons, target))
