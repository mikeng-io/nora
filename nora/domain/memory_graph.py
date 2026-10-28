"""Shared graph contract for design/unified-memory/27; no runtime wiring.

Entity ids are store-owned strings; event_entry is a Register row, event an
entity. Evidence snapshots are hydrated by the caller from sources actually
shown to the session, never accepted from model output.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol, get_args

EntityKind = Literal["person", "room", "unit", "nora", "place", "event", "work", "organisation"]
ENTITY_KINDS: tuple[str, ...] = get_args(EntityKind)
GraphRelation = Literal[
    "authored_by",
    "in_room",
    "attached_to",
    "shared_by",
    "reply_to",
    "reacted_to",
    "mentions",
    "located_in",
    "during",
    "outcome_of",
    "stayed_at",
    "co_located_with",
    "about",
    "records",
    "plans",
    "attended",
    "went_with",
    "at",
    "features",
    "likes",
    "dislikes",
    "works_at",
    "studies_at",
    "lives_in",
    "member_of",
    "concerns",
    "settles",
    "serves",
    "fulfils",
    "cancels",
    "same_as",
]
GRAPH_RELATIONS: tuple[str, ...] = get_args(GraphRelation)
Layer = Literal["record", "rollup", "notebook"]
RefusalReason = Literal[
    "vocabulary",
    "wrong_ends",
    "wrong_layer",
    "missing_evidence",
    "unseen_source",
    "notebook_evidence",
    "span_not_in_source",
    "scope_wider_than_evidence",
    "orphan_entity",
    "duplicate_entity",
]
Key = tuple[str, str]
ENTRY = (
    "note",
    "memo",
    "event_entry",
    "profile_line",
    "lesson",
    "standing",
    "decision",
    "commitment",
    "thread",
    "reminder",
)
RECORD = (
    "message",
    "edit",
    "delete",
    "reaction",
    "media",
    "link",
    "social",
    "social_media",
    "social_location",
    "web_page",
    "send",
    "document",
    "image",
    "tool_result",
    "session",
    "play",
    "reading",
    "rsvp",
)
ROLLUP = ("stay", "trip", "outing", "episode", "co_location", "daybook", "rhythm", "ties")


@dataclass(frozen=True)
class RelationRule:
    sources: tuple[str, ...]
    targets: tuple[str, ...]
    layer: Layer


def _rule(src: tuple[str, ...], dst: tuple[str, ...], layer: Layer = "notebook") -> RelationRule:
    return RelationRule(src, dst, layer)


RELATION_RULES: Mapping[str, RelationRule] = {
    "authored_by": _rule(
        ("message", "social", "send", "document", "image", "tool_result"),
        ("person", "nora"),
        "record",
    ),
    "in_room": _rule(("message", "link", *ENTRY), ("room",), "record"),
    "attached_to": _rule(
        ("reading", "media", "link", "social_media", "social_location"),
        ("message", "link", "social"),
        "record",
    ),
    "shared_by": _rule(("link", "media", "social_media"), ("person",), "record"),
    "reply_to": _rule(("message",), ("message",), "record"),
    "reacted_to": _rule(("reaction",), ("message", "social"), "record"),
    "mentions": _rule(("message", "social"), ("person", "place"), "record"),
    "located_in": _rule(("place", "outing"), ("place",), "rollup"),
    "during": _rule(("message", "media"), ROLLUP, "rollup"),
    "outcome_of": _rule(("episode",), ("send", "comment_reply", "summons_answer"), "rollup"),
    "stayed_at": _rule(("person",), ("place",), "rollup"),
    "co_located_with": _rule(("person",), ("person",), "rollup"),
    "about": _rule((*ENTRY, "reading", "web_page", "session", "play"), ENTITY_KINDS),
    "records": _rule(("event_entry",), ("event",)),
    "plans": _rule(("person",), ("event",)),
    "attended": _rule(("person",), ("event",)),
    "went_with": _rule(("person",), ("person",)),
    "at": _rule(("event",), ("place",)),
    "features": _rule(("event",), ("work", "person", "organisation")),
    "same_as": _rule(ENTITY_KINDS, ENTITY_KINDS),
}
for _rel in ("likes", "dislikes"):
    RELATION_RULES = {**RELATION_RULES, _rel: _rule(("person",), ("work", "place", "event"))}
for _rel in ("works_at", "studies_at", "lives_in", "member_of"):
    RELATION_RULES = {**RELATION_RULES, _rel: _rule(("person",), ("organisation", "place"))}
for _rel in ("concerns", "settles", "serves", "fulfils", "cancels"):
    RELATION_RULES = {**RELATION_RULES, _rel: _rule(ENTRY, (*ENTITY_KINDS, *ENTRY))}


@dataclass(frozen=True)
class Entity:
    id: str
    kind: str
    unit: str
    name: str
    aliases: tuple[str, ...] = ()
    attributes: Mapping[str, object] = field(default_factory=dict)
    state: str = "live"
    created_at: datetime | None = None
    merged_into: str | None = None

    @property
    def key(self) -> Key:
        return self.kind, self.id


@dataclass(frozen=True)
class Relation:
    id: str
    src: Key
    rel: str
    dst: Key
    layer: Layer
    unit: str
    persons: tuple[str, ...] = ()
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    asserted_at: datetime | None = None
    status: Literal["current", "superseded", "disputed", "retired"] = "current"
    written_by: str = "code"
    superseded_by: str | None = None
    created_at: datetime | None = None


@dataclass(frozen=True)
class Evidence:
    source: Key
    relation_id: str | None = None
    entity_id: str | None = None
    attribute: str | None = None
    role: Literal["supports", "contradicts"] = "supports"
    span: str | None = None

    def __post_init__(self) -> None:
        if not (
            (self.relation_id is not None and self.entity_id is None and self.attribute is None)
            or (
                self.relation_id is None
                and self.entity_id is not None
                and self.attribute is not None
            )
        ):
            raise ValueError("evidence targets one relation or one entity attribute")


@dataclass(frozen=True)
class Source:
    key: Key
    layer: Layer
    unit: str
    persons: tuple[str, ...] = ()
    text: str = ""


def validate_relation(
    relation: Relation, evidence: tuple[Evidence, ...], sources: Mapping[Key, Source]
) -> RefusalReason | None:
    """Pure admission check; persons are restrictions, so may narrow, never widen."""
    rule = RELATION_RULES.get(relation.rel)
    if rule is None:
        return "vocabulary"
    if relation.src[0] not in rule.sources or relation.dst[0] not in rule.targets:
        return "wrong_ends"
    if relation.layer != rule.layer:
        return "wrong_layer"
    if relation.layer == "notebook" and not evidence:
        return "missing_evidence"
    for item in evidence:
        source = sources.get(item.source)
        if source is None:
            return "unseen_source"
        if source.layer == "notebook" or source.key[0] not in (*RECORD, *ROLLUP):
            return "notebook_evidence"
        if item.span is not None and item.span not in source.text:
            return "span_not_in_source"
        if source.unit != relation.unit or not set(source.persons) <= set(relation.persons):
            return "scope_wider_than_evidence"
    return None


@dataclass(frozen=True)
class Refusal:
    kind: Literal["entity", "relation", "evidence"]
    id: str
    reason: RefusalReason


@dataclass(frozen=True)
class Admission:
    entities: tuple[Entity, ...] = ()
    relations: tuple[Relation, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    refused: tuple[Refusal, ...] = ()


@dataclass(frozen=True)
class Walk:
    unit: str
    persons: frozenset[str]
    depth: int = 2
    nodes: int = 100
    fan_out: int = 20
    layers: tuple[Layer, ...] = ("rollup", "notebook")
    valid_at: datetime | None = None
    relations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not 0 <= self.depth <= 4 or self.nodes < 1 or self.fan_out < 1:
            raise ValueError("walk requires depth 0..4 and positive node/fan-out caps")


@dataclass(frozen=True)
class GraphNode:
    key: Key
    entity: Entity | None = None
    evidence_count: int = 0


@dataclass(frozen=True)
class GraphEdge:
    relation: Relation
    evidence_count: int


@dataclass(frozen=True)
class Subgraph:
    status: Literal["ok", "partial", "unavailable"]
    nodes: tuple[GraphNode, ...] = ()
    relations: tuple[GraphEdge, ...] = ()


@dataclass(frozen=True)
class WorkItem:
    id: str
    source: Key
    unit: str
    input_hash: str
    as_of: datetime
    historical: bool = False
    state: Literal["pending", "running", "settled", "failed"] = "pending"
    attempts: int = 0
    last_error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    due_at: datetime | None = None
    settled_at: datetime | None = None


class MemoryGraph(Protocol):
    """Stage valid parts atomically; callers supply trusted shown-source snapshots.

    upsert_entity is for code identity pointers/updates; new model entities must
    enter through stage_and_admit, as endpoints of an admitted relation.
    Claims use attempt fencing and leases. Same source/hash is idempotent;
    changed hashes create new retained work. Live and historical claims are separate.
    """

    async def upsert_entity(self, entity: Entity) -> Entity: ...
    async def stage_and_admit(
        self,
        entities: tuple[Entity, ...],
        relations: tuple[Relation, ...],
        evidence: tuple[Evidence, ...],
        sources: Mapping[Key, Source],
    ) -> Admission: ...
    async def walk(self, seeds: tuple[Key, ...], query: Walk) -> Subgraph: ...
    async def enqueue(self, item: WorkItem) -> WorkItem: ...
    async def claim(
        self, *, historical: bool, now: datetime, lease_seconds: float
    ) -> WorkItem | None: ...
    async def settle(self, item: WorkItem, *, now: datetime) -> bool: ...
    async def fail(self, item: WorkItem, *, error: str, now: datetime) -> bool: ...
