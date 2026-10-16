"""What `social_read` shows of one person's recorded social items
(design/cognition-loop/22-social-stream.md §6): each item saying what it is
and how old it is, its caption as the author's published words, what its media
were read as, and its location only where the room may place the person.

**Two lineage kinds, both the declared predicates** (design/cognition-loop/10
§4, `nora.agent.lineage`). The item is `social` (P): room B may be shown it
when B lists P, so the tool resolves the person among the people B lists and
nobody else. A location tag places P somewhere at a time, so it is `presence`
(P): B's `location` flag AND the live DisclosureGuard. A room that may see the
item but not place the person gets the item without its location.

**R-18 and R-28.** An item whose caption or readings carry an identifier is
withheld whole, as the memory index's packet withholds one; a location that
carries an identifier or a coordinate is dropped alone.

**Bounded.** At most `SOCIAL_READ_MAX` items and `SOCIAL_READ_CHARS`
characters of caption, readings and location together, newest first; what
did not fit is counted, so the tool can say the answer was cut. One item is
bounded too, at `SOCIAL_ITEM_CHARS`: a carousel's readings past it are left
out and counted on the item as `more_seen`, so one long carousel neither
overruns the answer nor reads as everything it showed.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from nora.agent.egress import scan_identifiers, scan_reply
from nora.agent.lineage import entry_permits
from nora.domain.lineage import PRESENCE, LineageEntry, RenderRoom
from nora.domain.memory_index import social_label
from nora.domain.prompt import stamp
from nora.domain.social_stream import RecordedItem

__all__ = [
    "SOCIAL_ITEM_CHARS", "SOCIAL_READ_CHARS", "SOCIAL_READ_MAX", "location_shown",
    "render_items",
]

#: The most items one call returns, newest first.
SOCIAL_READ_MAX = 20
#: The most characters of caption, readings and location one call returns.
SOCIAL_READ_CHARS = 6000
#: The most characters of one item's caption, readings and location.
SOCIAL_ITEM_CHARS = SOCIAL_READ_CHARS // 2
#: The most characters of any one caption or reading.
_FIELD_CHARS = 800


def location_shown(person_id: str, target: RenderRoom) -> bool:
    """Whether room B may place `person_id`: the `presence` predicate."""
    return entry_permits(LineageEntry(PRESENCE, subject=person_id), target, origin=None)


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def _cut(text: str) -> str:
    text = text.strip()
    return text if len(text) <= _FIELD_CHARS else text[:_FIELD_CHARS].rstrip() + "…"


def _fit(readings: list[str], room: int) -> tuple[list[str], int]:
    """The readings, in media order, that fit in `room` characters, and how
    many were left out. In order, so what is shown is the carousel's start."""
    kept: list[str] = []
    for r in readings:
        if len(r) > room:
            break
        kept.append(r)
        room -= len(r)
    return kept, len(readings) - len(kept)


def render_items(
    items: Sequence[RecordedItem], *, by: str, place: bool, now: datetime,
    max_chars: int = SOCIAL_READ_CHARS,
) -> tuple[list[dict[str, Any]], int, int]:
    """`(shown, withheld, cut)`: the items in order as the model reads them,
    how many were withheld for an identifier, and how many did not fit in
    `max_chars`. `place` is `location_shown` for the author. An item whose
    readings did not all fit in `SOCIAL_ITEM_CHARS` carries `more_seen`, the
    count left out."""
    shown: list[dict[str, Any]] = []
    withheld = cut = 0
    spent = 0
    for it in items:
        caption = _cut(it.caption) if it.caption and it.caption.strip() else ""
        readings = [_cut(r) for r in it.perception if r.strip()]
        # Screened whole, before the cut: an identifier straddling the cut
        # would otherwise leave as a fragment the screen cannot recognise.
        if any(scan_identifiers(t) is not None for t in (it.caption or "", *it.perception)):
            withheld += 1
            continue
        location = (it.location or "").strip() if place else ""
        if location and scan_reply(location) is not None:
            location = ""
        readings, more_seen = _fit(
            readings, SOCIAL_ITEM_CHARS - len(caption) - len(location),
        )
        size = len(caption) + sum(len(r) for r in readings) + len(location)
        # Newest first, so once one item does not fit every older one is cut
        # too: an answer with a gap in its middle would read as complete.
        if cut or (shown and spent + size > max_chars):
            cut += 1
            continue
        spent += size
        item: dict[str, Any] = {
            "kind": social_label(it.network, it.kind, collab=it.collab),
            "by": by, "posted": stamp(_ms(it.taken_at), now),
        }
        if caption:
            item["caption"] = caption
        # What the media were read as; said, never left out, while unread.
        item["seen"] = readings or ("not shown" if more_seen else "not read yet")
        if more_seen:
            item["more_seen"] = more_seen
        if location:
            item["location"] = location
        shown.append(item)
    return shown, withheld, cut
