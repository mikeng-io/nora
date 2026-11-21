"""Inputs and explainable subgraph returned by a retrieval pass."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from nora.domain.lineage import LiveRoom
from nora.memory.domain.graph import ClaimPart, Kind, PointerKind

RetrievalStatus = Literal["ok", "partial", "nothing_found", "unavailable"]
RetrievalLimit = Literal["rows", "depth", "fan_out", "time_window"]


@dataclass(frozen=True, slots=True)
class ReadingRoom:
    platform: str
    conversation_id: str
    live: LiveRoom | None = None


@dataclass(frozen=True, slots=True)
class RetrievalScope:
    unit: str
    room: str
    is_dm: bool
    audience: frozenset[str]
    social_off: frozenset[str]
    as_of: datetime | None
    room_keys: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class RetrievalBounds:
    depth: int
    fan_out: int
    rows: int
    since: datetime
    until: datetime

    def __post_init__(self) -> None:
        if self.depth < 0 or self.fan_out < 1 or self.rows < 1:
            raise ValueError("invalid graph bounds")
        if self.since.tzinfo is None or self.until.tzinfo is None or self.since > self.until:
            raise ValueError("invalid graph time window")


@dataclass(frozen=True, slots=True)
class RetrievalAnchors:
    graph_ids: tuple[str, ...] = ()
    handles: tuple[str, ...] = ()
    query: str = ""


@dataclass(frozen=True, slots=True)
class EvidenceHandle:
    graph_id: str
    deleted_source: bool = False

    @property
    def handle(self) -> str:
        return f"graph:{self.graph_id}"


@dataclass(frozen=True, slots=True)
class RetrievedPerson:
    id: str | None
    role: str


@dataclass(frozen=True, slots=True)
class RetrievedEdge:
    type: str
    source: str
    target: str


@dataclass(frozen=True, slots=True)
class RetrievedSupport:
    count: int
    last_evidenced_at: datetime


@dataclass(frozen=True, slots=True)
class RetrievedLessonTally:
    support: int
    contradiction: int


@dataclass(frozen=True, slots=True)
class RetrievedRow:
    graph_id: str
    kind: Kind
    source: str | None
    basis: str
    reasoning: str | None
    confidence: str | None
    occurred_at: datetime | None
    asserted_at: datetime
    valid_from: datetime | None
    valid_to: datetime | None
    state: str
    origin_room: str | None
    exposure: str
    persons: tuple[RetrievedPerson, ...]
    content: object
    edges: tuple[RetrievedEdge, ...]
    evidence: tuple[EvidenceHandle, ...]
    close_when: Mapping[str, object]
    distance: int
    already_shown: bool
    unavailable: str | None = None
    part: ClaimPart | None = None
    pointer_kind: PointerKind | None = None
    support: RetrievedSupport | None = None
    items_since: int | None = None
    items_since_cause: str | None = None
    lesson_tally: RetrievedLessonTally | None = None


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    status: RetrievalStatus
    bounds: RetrievalBounds
    rows: tuple[RetrievedRow, ...] = ()
    limits: tuple[RetrievalLimit, ...] = ()
    cause: str | None = None
    new_count: int = 0


@dataclass(frozen=True, slots=True)
class Candidates:
    ids: tuple[str, ...] = ()
    outside_window: bool = False


@dataclass(frozen=True, slots=True)
class FreshCandidates:
    links: tuple[tuple[str, str], ...] = ()
    outside_window: bool = False


class RetrievalUnavailableError(Exception):
    """The scope or a required graph read could not be obtained."""


class ScopedGraph(Protocol):
    async def resolve_exact_seeds(self, graph_ids: tuple[str, ...]) -> Candidates: ...

    async def lexical(self, query: str) -> Candidates: ...

    async def vector(self, vector: list[float]) -> Candidates: ...

    async def walk_neighbours(self, graph_id: str, seen: frozenset[str]) -> Candidates: ...

    async def fresh_items(self, distances: Mapping[str, int]) -> FreshCandidates: ...

    async def load(
        self, distances: Mapping[str, int], already_shown: frozenset[str],
    ) -> tuple[RetrievedRow, ...]: ...


class RetrievalReader(Protocol):
    def open_retrieval(
        self, room: ReadingRoom, concerns: frozenset[str], as_of: datetime | None,
        bounds: RetrievalBounds, now: datetime,
    ) -> AbstractAsyncContextManager[ScopedGraph]: ...
