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

from typing import Literal, get_args

__all__ = [
    "HUB_KINDS",
    "NODE_KINDS",
    "NODE_STATES",
    "PROJECTION_OUTCOMES",
    "RELATIONS",
    "NodeKind",
    "ProjectionOutcome",
    "Relation",
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

#: `nora_memory_projection_total`'s `outcome`. `ok` projected (or dropped) in
#: the store's own transaction; `queued` failed, and the source is on
#: `memory_projection_queue` for the next pass; `lost` failed AND the queue
#: write failed, so only a rebuild finds it. In every case the store's own
#: write stands (60 §2).
ProjectionOutcome = Literal["ok", "queued", "lost"]
PROJECTION_OUTCOMES: tuple[str, ...] = get_args(ProjectionOutcome)
