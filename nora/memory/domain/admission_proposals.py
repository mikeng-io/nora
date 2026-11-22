"""The model's proposal operations and the facts admission may use."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar, Literal

from nora.domain.graph_registration import SourceKind
from nora.domain.lineage import Lineage
from nora.memory.domain.graph import Kind

ExposureOrder = ("external", "internal", "operator")
Occasion = Literal["called", "ambient", "proactive", "debrief", "wake", "review", "operator"]


@dataclass(frozen=True, slots=True)
class SourceReference:
    kind: SourceKind
    value: str


@dataclass(frozen=True, slots=True)
class PassContext:
    unit: str
    room: str | None
    room_is_dm: bool
    occasion: Occasion
    writer: str
    pass_id: str
    trace: str
    model: str
    rev_code: str
    rev_instruction: str
    rev_skill: str
    rev_config: str
    rev_identity: str
    rev_policy: str

    def provenance(self) -> dict[str, str]:
        return {
            "writer": self.writer, "occasion": self.occasion, "pass": self.pass_id,
            "trace": self.trace, "model": self.model, "rev_code": self.rev_code,
            "rev_instruction": self.rev_instruction, "rev_skill": self.rev_skill,
            "rev_config": self.rev_config, "rev_identity": self.rev_identity,
            "rev_policy": self.rev_policy,
        }


@dataclass(frozen=True, slots=True)
class RoomReference:
    id: str
    unit: str
    is_dm: bool = False


@dataclass(frozen=True, slots=True)
class ResolvedReferences:
    rooms: Mapping[str, RoomReference | None]
    persons: Mapping[str, str | None]


@dataclass(frozen=True, slots=True)
class PersonAuthor:
    id: str


@dataclass(frozen=True, slots=True)
class RowFacts:
    id: str
    kind: Kind
    basis: str
    state: str
    unit: str
    origin_room: str | None = None
    room_is_dm: bool = False
    persons: tuple[str | None, ...] = ()
    excluded_persons: tuple[str, ...] = ()
    exposure: str = "external"
    lineage: Lineage = field(default_factory=Lineage)
    form: str | None = None
    trigger: object = None
    delivery_mode: str | None = None
    pointer_kind: str | None = None
    source: str | None = None
    author: PersonAuthor | Literal["nora"] | None = None
    dependencies: tuple[str, ...] = ()
    source_ref: SourceReference | None = None
    fetched_url: str | None = None
    table: str | None = None
    evidence_at: datetime | None = None
    verdict_ending: str | None = None
    aimed_at_nora: bool = False
    pointer_ref: str | None = None
    part: str | None = None


@dataclass(frozen=True, slots=True)
class Scope:
    unit: str
    origin_room: str | None = None
    persons: tuple[str, ...] = ()
    excluded_persons: tuple[str, ...] = ()
    # None inherits the restrictions of the base and the evidence.
    exposure: str | None = "external"


@dataclass(frozen=True, slots=True, kw_only=True)
class Link:
    type: str
    source: str
    target: str
    basis: str
    reasoning: str | None = None
    confidence: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ProposalFields:
    id: str
    basis: str
    scope: Scope
    reasoning: str | None = None
    confidence: str | None = None
    citations: tuple[str, ...] = ()
    provenance: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateClaim(ProposalFields):
    kind: ClassVar[Kind] = "claim"
    content: object
    part: str = "fact"
    prediction: str | None = None
    window_end: datetime | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    links: tuple[Link, ...] = ()
    replace_about: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateEntity(ProposalFields):
    kind: ClassVar[Kind] = "entity"
    name: str
    aliases: tuple[str, ...] = ()
    pointer_kind: str | None = None
    pointer_ref: str | None = None
    links: tuple[Link, ...] = ()
    source_ref: SourceReference | None = None


@dataclass(frozen=True, slots=True)
class Condition:
    form: str
    value: object


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateIntention(ProposalFields):
    kind: ClassVar[Kind] = "intention"
    owner: str
    owed_to: str
    form: str
    condition: Condition
    drop_when: Condition | None = None
    deadline: datetime | None = None
    review_time: datetime | None = None
    delivery_mode: str = "when_it_comes_up"
    trigger: object = None
    links: tuple[Link, ...] = ()
    rooms: tuple[str, ...] = ()
    replace_about: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class AddEdge(ProposalFields):
    kind: ClassVar[Kind] = "edge"
    type: str
    source: str
    target: str


Create = CreateClaim | CreateEntity | CreateIntention | AddEdge


@dataclass(frozen=True, slots=True, kw_only=True)
class Revise:
    base: str
    replacement: Create


@dataclass(frozen=True, slots=True, kw_only=True)
class Retire(ProposalFields):
    base: str
    ending: str | None = None
    expected_kind: Kind | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class CloseIntention(ProposalFields):
    witness: str


@dataclass(frozen=True, slots=True, kw_only=True)
class MergeEntities(ProposalFields):
    source: str
    survivor: str


Proposal = Create | Revise | Retire | MergeEntities | CloseIntention
