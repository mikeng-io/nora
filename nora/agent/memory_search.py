"""The one retrieval service over the memory index (design/unified-memory/
25-memory-graph.md §4, 60-build-step-3.md S5): scope, seed, expand hop by
hop, rank, and an evidence packet with the path that reached each item.
`ask_memory` calls it while `[memory_index] enabled`; nothing else does yet.

**1. Scope, per read, from the live policy** (`scope_for`). The unit is the
read boundary (DEC-231, 35 §3): the reading room's unit, every room of it,
the persons this room lists, who is here (listed or in the live roster), and
the listed persons whose presence passes here (the room's `location` flag
and the live DisclosureGuard, `nora.agent.lineage`'s own predicate). Lineage
mode is not consulted: it is off and stays off (35 §3). The arrays go DOWN
to the index, which applies them inside every read (`SqlMemoryIndex`); the
walk never filters afterwards, so it can only step onto in-scope nodes and a
path is cut at its first out-of-scope node.

**2. Seeds.** Lexical, by the question's terms; plus, for a named person
this room lists, the in-scope nodes `about` them (25 §4 step 2's exact
reference). A person this room does not list seeds nothing. A hub never
seeds.

**3. Expansion**, hop by hop, both edge directions, from the best
`FRONTIER_WIDTH` nodes of the last hop. Two rules on top of scope:

- **The hub rule** (25 §4 step 3): a person, room, unit, Nora or place node
  is kept only as a direct neighbour of the node that reached it (a message's
  author, an event's venue), and never put on the next frontier. Two hops
  through a person reach everything ever said about them, which is a search
  on the person, not an expansion of the question.
- **The listed-room rule** (35 §3): an edge to person P is followed only if
  the reading room lists P and, when the node it comes from was said in a
  room, that room lists P too. Talk about Kit in a room Kit is not in is not
  evidence about Kit.

Each step multiplies the parent's relevance by the relation's weight
(`REL_WEIGHT`). A node below `RELEVANCE_FLOOR` is not kept, and the walk
stops early when a hop keeps nothing (adaptive depth), at `NODE_BUDGET`
nodes, or at the hop count `depth` asks for, never past `HOP_CEILING`.

**4. Rank** by relevance times age decay (`HALF_LIFE_DAYS`). **5. Return** the
best `RESULT_LIMIT` items, each with its kind, age, a handle where the kind
has one a person or a tool can use, its text, and its path; an item whose
text carries an identifier is withheld whole (`scan_identifiers`, R-18).

**Statuses** (R-27, 25 §4): `unavailable` on any error, counted, never an
empty answer; `partial` when the index is not `complete` or its projection
queue is not empty, whatever was found, so an unfinished index is never
«you never said that»; `nothing_found` only from a complete index; `ok`.

The constants below are STARTING values (25 §4: adaptive depth ships only if
it beats a fixed two hops on the journey slices); none was measured.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from nora.agent.egress import scan_identifiers
from nora.agent.lineage import section_permits
from nora.domain.external_files import render_file_links
from nora.domain.lineage import PRESENCE, ROW, Lineage, LineageEntry, RenderRoom
from nora.domain.media import HANDLE_CHARS
from nora.domain.memory_index import (
    EPISODE_KINDS,
    IndexedNode,
    MemoryIndexRead,
    MemoryScope,
    SearchStatus,
)
from nora.domain.prompt import stamp
from nora.domain.rollup import room_key

__all__ = [
    "HOPS_BY_DEPTH",
    "HOP_CEILING",
    "MemorySearch",
    "MemorySearchResult",
    "render_packet",
    "scope_for",
    "terms_of",
]

log = logging.getLogger("nora.agent.memory_search")

_METRIC = "nora_memory_search_total"

#: `ask_memory`'s existing `depth` as hops (60 §0.7). Two by default.
HOPS_BY_DEPTH: Mapping[str, int] = {
    "minimal": 2, "low": 2, "medium": 3, "high": 4, "max": 4,
}
#: 25 §4 step 3's hard ceiling.
HOP_CEILING = 4
#: A node whose relevance falls below this is not kept; a hop that keeps
#: none ends the walk. Starting value: a lexical seed scores at least
#: `WORD_SIMILARITY_MIN` (0.5), so this admits about four weak hops from the
#: weakest seed and stops the walk well before noise dominates.
RELEVANCE_FLOOR = 0.1
#: Nodes kept per search, seeds included.
NODE_BUDGET = 60
#: Nodes expanded per hop: the best of the last hop's.
FRONTIER_WIDTH = 20
SEED_LIMIT = 20
RESULT_LIMIT = 10
#: An exact-reference seed (a node `about` the named person) has no lexical
#: score of its own; it ranks as a middling lexical hit.
PERSON_SEED_SCORE = 0.6
#: Age decay: an item this many days old ranks at half its relevance.
HALF_LIFE_DAYS = 180.0
#: Characters of one item's text in the packet.
TEXT_CHARS = 600

#: How much of the parent's relevance a relation carries (25 §4 step 3,
#: «weighted by relation»). Provenance relations carry most; co-occurrence
#: least. A relation absent here carries `_DEFAULT_WEIGHT`.
REL_WEIGHT: Mapping[str, float] = {
    "cites": 0.9, "attached_to": 0.9, "supersedes": 0.8, "tracks": 0.8,
    "closes_into": 0.8, "leaves_behind": 0.8, "draws_on": 0.7, "informed_by": 0.7,
    "disputes": 0.8, "feedback_on": 0.7, "about": 0.6, "during": 0.5,
    "outcome_of": 0.6, "mentions": 0.5, "in_room": 0.3,
}
_DEFAULT_WEIGHT = 0.5

#: Words that carry no search: the question's grammar, not its subject.
_STOP = frozenset({
    "about", "also", "and", "any", "are", "been", "but", "can", "could", "did", "does", "for",
    "from", "had", "has", "have", "her", "him", "his", "how", "into", "its", "just", "know",
    "like", "not", "our", "remember", "said", "say", "she", "some", "tell", "than", "that",
    "the", "their", "them", "then", "there", "they", "this", "was", "were", "what", "when",
    "where", "which", "who", "whom", "why", "will", "with", "would", "you", "your"
})
#: A Latin word, or a run of CJK ideographs (the two BMP blocks).
_WORD = re.compile(
    "[a-z0-9][a-z0-9'\\-]*[a-z0-9]|[\u3400-\u9fff\uf900-\ufaff]+", re.IGNORECASE,
)
_MAX_TERMS = 8


def terms_of(query: str) -> list[str]:
    """The question's search terms: its words of three or more letters that
    are not grammar, and each run of CJK characters whole (a Cantonese run is
    one term; pg_trgm's word similarity finds it inside a sentence,
    `record_query`'s measured case)."""
    out: list[str] = []
    for m in _WORD.finditer(query or ""):
        word = m.group(0)
        if word.isascii():
            word = word.lower().removesuffix("'s")
            if len(word) < 3 or word in _STOP:
                continue
        out.append(word)
    return list(dict.fromkeys(out))[:_MAX_TERMS]


def scope_for(target: RenderRoom) -> MemoryScope | None:
    """The reading room's scope, from the live policy (`target.rooms`) and
    its live roster (`target.live`); `None` for a room outside the policy,
    which reads nothing."""
    here = target.room
    if here is None:
        return None
    unit_rooms = [r for r in target.rooms if here.unit and r.unit == here.unit]
    if not any(r.platform == here.platform and r.ids == here.ids for r in unit_rooms):
        unit_rooms.append(here)
    room_persons = {
        room_key(r.platform, cid): r.persons or frozenset()
        for r in unit_rooms for cid in sorted(r.ids)
    }
    listed = here.persons or frozenset()
    live = target.live
    vouched = (
        here.persons is not None and live is not None and live.available
        and not live.degraded and live.unnamed == 0
    )
    presence = frozenset(
        p for p in listed
        if section_permits((LineageEntry(PRESENCE, subject=p),), target, origin=None)
    )
    return MemoryScope(
        units=(here.unit,) if here.unit else (),
        rooms=tuple(room_persons),
        listed_here=listed,
        people_here=listed | (live.person_ids if live is not None else frozenset()),
        exclude_strict=not vouched,
        presence_persons=presence,
        room_persons=room_persons,
    )


@dataclass(frozen=True, slots=True)
class Step:
    """One step of a path: the node's kind, and how it was reached from the
    step before (`None` on the seed). `how` is `matched` or `about` on a
    seed."""

    kind: str
    rel: str | None = None
    outgoing: bool = True
    how: str = ""


@dataclass(slots=True)
class Hit:
    node: IndexedNode
    score: float
    path: tuple[Step, ...]
    people: set[str] = field(default_factory=set)
    places: set[str] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class MemorySearchResult:
    status: SearchStatus
    hits: tuple[Hit, ...] = ()


def _listed_room_rule(person: str, via: IndexedNode, scope: MemoryScope) -> bool:
    """An edge to `person` is followed only if this room lists them and the
    node it comes from was said in a room that lists them (35 §3)."""
    if person not in scope.listed_here:
        return False
    if via.origin_room is None:
        return True
    return person in scope.room_persons.get(via.origin_room, frozenset())


def _decay(at_ms: int | None, now_ms: int) -> float:
    if at_ms is None:
        return 0.5
    days = max(0.0, (now_ms - at_ms) / 86_400_000)
    return math.pow(0.5, days / HALF_LIFE_DAYS)


async def walk(
    index: MemoryIndexRead, scope: MemoryScope, *, terms: Sequence[str],
    person_id: str | None, hops: int, admits: Callable[[IndexedNode], bool],
) -> list[Hit]:
    """Seed, then expand hop by hop (module docstring). `admits` is the
    per-row presence check an episode must pass (R-31); every other rule is
    the index's scope or this function's."""
    reached: dict[tuple[str, str], Hit] = {}

    def keep(node: IndexedNode, score: float, path: tuple[Step, ...]) -> bool:
        if node.key in reached or score < RELEVANCE_FLOOR:
            return False
        if node.kind in EPISODE_KINDS and not admits(node):
            return False
        reached[node.key] = Hit(node, score, path)
        return True

    for s in await index.seed(terms, scope, limit=SEED_LIMIT) if terms else ():
        keep(s.node, s.score, (Step(s.node.kind, how="matched"),))
    if person_id and person_id in scope.listed_here:
        for node in await index.about(person_id, scope, limit=SEED_LIMIT):
            keep(node, PERSON_SEED_SCORE, (Step(node.kind, how="about"),))

    frontier = sorted(reached.values(), key=lambda h: -h.score)[:FRONTIER_WIDTH]
    for _ in range(max(0, min(hops, HOP_CEILING))):
        if not frontier or len(reached) >= NODE_BUDGET:
            break
        found = await index.neighbours([h.node.key for h in frontier], scope)
        added: list[Hit] = []
        for nb in sorted(found, key=lambda n: (n.via, n.rel, n.node.key)):
            parent = reached.get(nb.via)
            if parent is None:
                continue
            if nb.node.is_hub:
                # The hub rule: kept on the node that reached it, never
                # expanded from. A person only under the listed-room rule.
                if nb.node.kind == "person":
                    if _listed_room_rule(nb.node.source_id, parent.node, scope):
                        parent.people.add(nb.node.source_id)
                elif nb.node.kind == "place" and nb.node.text:
                    parent.places.add(nb.node.text)
                continue
            score = parent.score * REL_WEIGHT.get(nb.rel, _DEFAULT_WEIGHT)
            step = Step(nb.node.kind, nb.rel, nb.outgoing)
            if keep(nb.node, score, (*parent.path, step)):
                added.append(reached[nb.node.key])
                if len(reached) >= NODE_BUDGET:
                    break
        if not added:
            break  # adaptive: this hop found nothing above the floor
        frontier = sorted(added, key=lambda h: -h.score)[:FRONTIER_WIDTH]
    return list(reached.values())


class MemorySearch:
    """`search` for one read. Takes the index and the metrics registry."""

    def __init__(self, index: MemoryIndexRead, metrics: Any) -> None:
        self._index = index
        self._metrics = metrics

    def _count(self, status: SearchStatus) -> MemorySearchResult:
        self._metrics.inc(_METRIC, status=status)
        return MemorySearchResult(status)

    async def search(
        self, *, query: str, person_id: str | None, target: RenderRoom, depth: str,
        now_ms: int,
    ) -> MemorySearchResult:
        scope = scope_for(target)
        if scope is None:
            return self._count("unavailable")

        def admits(node: IndexedNode) -> bool:
            lineage = Lineage.from_json(node.lineage)
            return section_permits(lineage.section(ROW), target, origin=None)

        try:
            state = await self._index.index_state()
            hits = await walk(
                self._index, scope, terms=terms_of(query), person_id=person_id,
                hops=HOPS_BY_DEPTH.get(depth, 2), admits=admits,
            )
        except Exception as exc:  # noqa: BLE001 — fail closed: «I couldn't check»
            log.warning("memory search failed (%s)", type(exc).__name__)
            return self._count("unavailable")
        ranked = sorted(
            hits, key=lambda h: (-h.score * _decay(h.node.at_ms, now_ms), h.node.key),
        )[:RESULT_LIMIT]
        incomplete = state.status != "complete" or state.queue > 0
        status: SearchStatus = (
            "partial" if incomplete else "ok" if ranked else "nothing_found"
        )
        self._metrics.inc(_METRIC, status=status)
        return MemorySearchResult(status, tuple(ranked))


def _path(path: Sequence[Step]) -> list[str]:
    """`["message (matched)", "<-cites- memo"]`: each step's kind and the
    relation that reached it, arrowed the way the edge points. No id."""
    out: list[str] = []
    for s in path:
        if s.rel is None:
            out.append(f"{s.kind} ({s.how})")
        else:
            out.append(f"-{s.rel}-> {s.kind}" if s.outgoing else f"<-{s.rel}- {s.kind}")
    return out


def _handle(node: IndexedNode) -> str | None:
    """What a person or a tool can name the item by: an attachment's media
    handle, an entry's title (its first line). A message, an episode and an
    account have none: their ids are not something said in a chat (R-18)."""
    if node.kind == "media" and node.sha256:
        return node.sha256[:HANDLE_CHARS]
    if node.kind in ("note", "memo", "event"):
        return node.text.split("\n", 1)[0].strip() or None
    return None


def render_packet(
    hits: Sequence[Hit], *, name_of: Callable[[str], str],
    room_label: Callable[[str], str], now: datetime,
) -> tuple[list[dict[str, Any]], int]:
    """The evidence packet (25 §4 step 5) and how many items were withheld
    for carrying an identifier. Names, room labels and handles only: never a
    person id, a room key or a row id."""
    items: list[dict[str, Any]] = []
    withheld = 0
    for h in hits:
        text = render_file_links(h.node.text[:TEXT_CHARS]) if h.node.text else ""
        places = sorted(p for p in h.places if scan_identifiers(p) is None)
        if text and scan_identifiers(text) is not None:
            withheld += 1
            continue
        item: dict[str, Any] = {"kind": h.node.kind}
        if h.node.at_ms is not None:
            item["age"] = stamp(h.node.at_ms, now)
        handle = _handle(h.node)
        if handle:
            item["handle"] = handle
        if h.node.origin_room:
            item["room"] = room_label(h.node.origin_room)
        item["text"] = text
        people = sorted({name_of(p) for p in h.people})
        if people:
            item["people"] = people
        if places:
            item["places"] = places
        item["path"] = _path(h.path)
        items.append(item)
    return items, withheld
