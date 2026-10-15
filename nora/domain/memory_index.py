"""The memory index's closed vocabularies (design/unified-memory/25-memory-graph.md
§2, 60-build-step-3.md §1).

One place for the node kinds, the relations and the node states, read by the
table's CHECKs (`nora.infra.sql.schema`), by the projector
(`nora.infra.sql.memory_index`) and by the projection counter's help text, so
the three cannot disagree. A new relation or kind is a schema change, never
something a model invents (25 §2): adding one here without its migration
fails `tests/infra/test_schema_parity.py`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, get_args

__all__ = [
    "EMBEDDED_KINDS",
    "EMBED_OUTCOMES",
    "EPISODE_KINDS",
    "HUB_KINDS",
    "NODE_KINDS",
    "NODE_STATES",
    "PERSON_SCOPED_KINDS",
    "PROJECTION_OUTCOMES",
    "RELATIONS",
    "SEARCH_STATUSES",
    "EmbedOutcome",
    "IndexState",
    "IndexedNode",
    "MemoryIndexRead",
    "MemoryScope",
    "Neighbour",
    "NodeKind",
    "ProjectionOutcome",
    "Relation",
    "SearchStatus",
    "Seed",
    "embeds_bytes",
    "in_scope",
]

#: Every kind step 3 names (60 §3), so S2-S4 add projectors without a CHECK
#: migration. `media` is one attachment, `(message, sha256)`, never the bytes.
NodeKind = Literal[
    "message", "media", "note", "memo", "event", "lesson", "character",
    "outing", "stay", "trip", "person", "room", "unit", "nora", "place",
]
NODE_KINDS: tuple[str, ...] = get_args(NodeKind)

#: The entities (25 §1): a node a hop may reach only as a direct neighbour,
#: never fan out from (25 §4 step 3). They are the only nodes allowed no
#: scope of their own; every other node carries a unit, a room or a person.
HUB_KINDS: tuple[str, ...] = ("person", "room", "unit", "nora", "place")

#: 25 §2's closed set, as written there.
Relation = Literal[
    "authored_by", "in_room", "shared_by", "attached_to", "during", "about",
    "cites", "supersedes", "tracks", "closes_into", "leaves_behind",
    "draws_on", "informed_by", "disputes", "feedback_on", "depicts",
    "mentions", "located_in", "outcome_of",
]
RELATIONS: tuple[str, ...] = get_args(Relation)

NODE_STATES: tuple[str, ...] = ("live", "retired", "superseded")

#: The kinds that get a vector (25 §7's «vector» column, 65 §1.4): every
#: kind with text worth matching by meaning. Never a hub (a hub never seeds,
#: 60 S5), and never a kind 25 §7 marks lexical only: the Rollup episodes
#: (outing, stay, trip), whose text is a kind and an interval. Disputes and
#: feedback, also lexical only there, have no node kind yet.
EMBEDDED_KINDS: tuple[str, ...] = (
    "message", "media", "note", "memo", "event", "lesson", "character",
)

#: `nora_memory_embed_total`'s `outcome`, one per node the embedding worker
#: tried (65 V1). `ok` stored a vector; `unavailable` the embedder (or the
#: blob store holding an image's bytes) did not answer, and the tick stops;
#: `rejected` the embedder refused this input; `empty` the node had nothing
#: to embed (no text, and no image bytes). Every failure leaves the node's
#: vector NULL, counts an attempt and backs the node off.
EmbedOutcome = Literal["ok", "unavailable", "rejected", "empty"]
EMBED_OUTCOMES: tuple[str, ...] = get_args(EmbedOutcome)


def embeds_bytes(media_type: str) -> bool:
    """Whether an attachment of `media_type` is embedded from its bytes
    rather than its reading (65 §1.3): an image, a sticker or a GIF is; a
    video, a voice note or a document is embedded from its reading's text."""
    return media_type.strip().lower().startswith("image/")


#: `nora_memory_projection_total`'s `outcome`. `ok` projected (or dropped) in
#: the store's own transaction; `queued` failed, and the source is on
#: `memory_projection_queue` for the next pass; `lost` failed AND the queue
#: write failed, so only a rebuild finds it. In every case the store's own
#: write stands (60 §2).
ProjectionOutcome = Literal["ok", "queued", "lost"]
PROJECTION_OUTCOMES: tuple[str, ...] = get_args(ProjectionOutcome)


# --- the read (S5, design/unified-memory/25-memory-graph.md §4) --------------

#: `nora_memory_search_total`'s `status`, and what `ask_memory` answers while
#: `[memory_index] enabled` (25 §4): `partial` while a rebuild has not
#: completed or the projection queue is not empty, never `nothing_found`;
#: `unavailable` on any error (fail closed).
SearchStatus = Literal["ok", "nothing_found", "partial", "unavailable"]
SEARCH_STATUSES: tuple[str, ...] = get_args(SearchStatus)

#: The Rollup episodes, each carrying a presence lineage the reading room must
#: pass (R-31, DEC-222, 60 §0.3).
EPISODE_KINDS: tuple[str, ...] = ("outing", "stay", "trip")

#: The person-only kinds: no unit and no room, readable where a room lists
#: their person (60 §0.3). Any other node with neither a unit nor a room is
#: never in scope.
PERSON_SCOPED_KINDS: tuple[str, ...] = ("stay", "trip")


@dataclass(frozen=True, slots=True)
class MemoryScope:
    """What one read may reach, computed per read from the live policy and
    the reading room's live roster (25 §4 step 1, 35 §3). The unit is the
    boundary: a node is in scope when it belongs to the reading room's unit
    or to one of that unit's rooms (or, for a stay or trip, to a person this
    room lists), every person it is readable only to is listed here, and it
    names nobody excluded from here.

    - `units`, `rooms`: the reading room's unit and every room key of it.
    - `listed_here`: the persons the reading room lists (empty for a `*` room).
    - `people_here`: who is here, listed or in the live roster: an
      `exclude_persons` naming any of them keeps a node out.
    - `exclude_strict`: the room cannot vouch for who is in it (a `*` room,
      an unmapped account, an unreadable or degraded roster): any node with
      an `exclude_persons` is out (`nora.agent.lineage.excluded`'s rule).
    - `presence_persons`: listed persons whose presence passes here (the
      room's `location` flag AND the live DisclosureGuard): an episode is in
      scope only if every person of it is one.
    - `room_persons`: each room of the unit and the persons it lists, for the
      listed-room rule (evidence about P only from rooms that list P).
    """

    units: tuple[str, ...]
    rooms: tuple[str, ...]
    listed_here: frozenset[str]
    people_here: frozenset[str]
    exclude_strict: bool
    presence_persons: frozenset[str]
    room_persons: Mapping[str, frozenset[str]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class IndexedNode:
    """One `memory_node` row as a read returns it: a pointer and what is
    needed to find and show it, never the item's truth (25 §1)."""

    kind: str
    source_id: str
    text: str = ""
    at_ms: int | None = None
    unit: str | None = None
    origin_room: str | None = None
    persons: tuple[str, ...] = ()
    exclude_persons: tuple[str, ...] = ()
    lineage: Any = None
    sha256: str | None = None
    state: str = "live"

    @property
    def key(self) -> tuple[str, str]:
        return (self.kind, self.source_id)

    @property
    def is_hub(self) -> bool:
        return self.kind in HUB_KINDS


@dataclass(frozen=True, slots=True)
class Seed:
    """A node a search starts from, with its lexical relevance in [0, 1]."""

    node: IndexedNode
    score: float


@dataclass(frozen=True, slots=True)
class Neighbour:
    """`node`, reached from the frontier node `via` along `rel`: `outgoing`
    when the edge is `via -rel-> node`, else `node -rel-> via`."""

    via: tuple[str, str]
    rel: str
    outgoing: bool
    node: IndexedNode


@dataclass(frozen=True, slots=True)
class IndexState:
    """`memory_index_state.status` (`never`, `building`, `complete`) and the
    projection queue's length now."""

    status: str
    queue: int


def in_scope(node: IndexedNode, scope: MemoryScope) -> bool:
    """The scope predicate for a NON-hub node, in Python: the fake index's,
    and the statement of what `SqlMemoryIndex` writes in SQL (the contract
    suite holds the two to the same answers). A hub never passes: it has no
    scope, and is reached only as a direct neighbour (25 §4 step 3)."""
    if node.kind in HUB_KINDS or node.state != "live":
        return False
    placed = (
        (node.unit is not None and node.unit in scope.units)
        or (node.origin_room is not None and node.origin_room in scope.rooms)
        or (
            node.unit is None and node.origin_room is None
            and node.kind in PERSON_SCOPED_KINDS
        )
    )
    if not placed:
        return False
    if not set(node.persons) <= scope.listed_here:
        return False
    if node.exclude_persons and (
        scope.exclude_strict or set(node.exclude_persons) & scope.people_here
    ):
        return False
    return not (
        node.kind in EPISODE_KINDS and not set(node.persons) <= scope.presence_persons
    )


class MemoryIndexRead(Protocol):
    """The read half of the memory index (25 §4): `SqlMemoryIndex`, and
    `nora.testing.memory_index.FakeMemoryIndex` under the shared contract
    suite. Every method applies `scope` INSIDE its read (25 §3): a node out
    of scope is never returned, so nothing after the read has to remember
    to drop it."""

    async def seed(
        self, terms: Sequence[str], scope: MemoryScope, *, limit: int,
    ) -> list[Seed]:
        """In-scope, non-hub nodes whose text holds the terms (pg_trgm word
        similarity, or containment), best first. A hub never seeds."""
        ...

    async def about(
        self, person_id: str, scope: MemoryScope, *, limit: int,
    ) -> list[IndexedNode]:
        """In-scope, non-hub nodes with an `about` edge to `person_id`, newest
        first: the exact-reference seeds of a named person."""
        ...

    async def neighbours(
        self, frontier: Sequence[tuple[str, str]], scope: MemoryScope,
    ) -> list[Neighbour]:
        """Every node one edge from a frontier node, in either direction,
        that exists (a dangling edge drops itself) and is in scope; a hub
        node is returned as a direct neighbour whatever its scope."""
        ...

    async def index_state(self) -> IndexState:
        ...
