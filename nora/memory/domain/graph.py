"""The Cognitive Graph's closed vocabularies and the pure edge and citation
rules (design/cognition/graph.md).

Every value set is a `Literal`; the schema declares the same sets again as
CHECK constraints in `nora/infra/sql/cognitive_graph_schema.py`, and the unit
tests compare them — a vocabulary change lands in both places or fails. The
rules here are pure functions: given the vocabularies they decide which
endpoints and citations are legal, so the same rule holds at admission and at
read time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from nora.domain.graph_registration import (
    Exposure as Exposure,
)
from nora.domain.graph_registration import (
    StaleBaseError as StaleBaseError,
)

#: Bases (graph.md «the planes»): `authored` rows live outside the graph; the
#: other four are what a graph row or edge may carry.
Basis = Literal["authored", "observed", "computed", "inferred", "committed"]
GraphBasis = Literal["observed", "computed", "inferred", "committed"]
EntityBasis = Literal["computed", "inferred"]
ClaimBasis = Literal["inferred"]
IntentionBasis = Literal["committed"]

#: Row kinds registered through `registry`; `item` and `aggregate` arrive with
#: the later phases that create their tables.
Kind = Literal["entity", "item", "aggregate", "claim", "intention", "edge"]

Reasoning = Literal["told", "deduced", "induced", "abduced"]
Confidence = Literal["low", "medium", "high"]

ClaimPart = Literal["fact", "trait", "guess"]
PointerKind = Literal["person", "room", "unit", "nora"]

ConditionForm = Literal[
    "act_event", "person_event", "predicate", "deadline", "untyped",
]
#: A done-when admits every condition form but `deadline`; the deadline is the
#: `deadline` column.
DoneWhenForm = Literal["act_event", "person_event", "predicate", "untyped"]
IntentionForm = Literal["achieve", "maintain"]
AchieveEnding = Literal["achieved", "dropped", "leaked", "unknown"]
MaintainEnding = Literal["ceased", "dropped"]
DeliveryMode = Literal["choose_moment", "when_it_comes_up"]

RegistryState = Literal["live", "tombstoned"]
EntityState = Literal["admitted", "merged", "retired", "tombstoned"]
ClaimState = Literal[
    "live", "superseded", "hidden", "awaiting_weighing", "retired", "tombstoned",
]
IntentionState = Literal["open", "ended", "superseded", "tombstoned"]
EdgeState = Literal["live", "superseded", "retired", "tombstoned"]

#: Edge families: the three indexable relations, and a typed relation for
#: every other declared edge type.
EdgeFamily = Literal["cites", "about", "draws_on", "relation"]
EdgeType = Literal[
    "cites", "about", "draws_on", "rests_on", "serves", "watches", "closed_by",
    "interested_in", "merged_into",
]


@dataclass(frozen=True, slots=True)
class EdgeDeclaration:
    """One closed edge type: its family, the kinds each endpoint may be and
    the bases the edge itself may carry."""

    family: EdgeFamily
    source_kinds: frozenset[str]
    target_kinds: frozenset[str]
    bases: frozenset[str]


#: The closed edge vocabulary (graph.md «edges»). `cites` is the only support
#: edge; `about` is the retrieval index; `draws_on` links one claim or
#: intention to another; the rest are typed relations.
EDGES: dict[str, EdgeDeclaration] = {
    "cites": EdgeDeclaration(
        family="cites",
        source_kinds=frozenset({"claim", "intention", "aggregate", "entity"}),
        target_kinds=frozenset({"item", "aggregate", "entity"}),
        bases=frozenset({"computed", "inferred", "committed"}),
    ),
    "about": EdgeDeclaration(
        family="about",
        source_kinds=frozenset(
            {"entity", "item", "aggregate", "claim", "intention", "edge"}
        ),
        target_kinds=frozenset({"entity"}),
        bases=frozenset({"computed"}),
    ),
    "draws_on": EdgeDeclaration(
        family="draws_on",
        source_kinds=frozenset({"claim", "intention"}),
        target_kinds=frozenset({"claim", "intention"}),
        bases=frozenset({"inferred"}),
    ),
    "rests_on": EdgeDeclaration(
        family="relation",
        source_kinds=frozenset({"intention"}),
        target_kinds=frozenset({"claim", "aggregate"}),
        bases=frozenset({"committed"}),
    ),
    "serves": EdgeDeclaration(
        family="relation",
        source_kinds=frozenset({"intention"}),
        target_kinds=frozenset({"intention"}),
        bases=frozenset({"committed"}),
    ),
    "watches": EdgeDeclaration(
        family="relation",
        source_kinds=frozenset({"intention"}),
        target_kinds=frozenset({"entity"}),
        bases=frozenset({"committed"}),
    ),
    "closed_by": EdgeDeclaration(
        family="relation",
        source_kinds=frozenset({"intention"}),
        target_kinds=frozenset({"item", "aggregate"}),
        bases=frozenset({"computed"}),
    ),
    "merged_into": EdgeDeclaration(
        family="relation",
        source_kinds=frozenset({"entity"}),
        target_kinds=frozenset({"entity"}),
        bases=frozenset({"inferred"}),
    ),
    "interested_in": EdgeDeclaration(
        family="relation",
        source_kinds=frozenset({"entity"}),
        target_kinds=frozenset({"entity"}),
        bases=frozenset({"inferred"}),
    ),
}


def endpoints_legal(edge_type: str, source_kind: str, target_kind: str) -> bool:
    """An edge type admits only the endpoint kinds its declaration names."""
    declaration = EDGES.get(edge_type)
    return (
        declaration is not None
        and source_kind in declaration.source_kinds
        and target_kind in declaration.target_kinds
    )


def basis_legal(edge_type: str, basis: str) -> bool:
    """An edge carries only the bases its type's declaration names."""
    declaration = EDGES.get(edge_type)
    return declaration is not None and basis in declaration.bases


def is_support(edge_type: str) -> bool:
    """`cites` is the only edge a row may rest its truth on."""
    return edge_type == "cites"


@dataclass(frozen=True, slots=True)
class Cited:
    """What a `cites` or `rests_on` edge's target names: a row, or a part of
    an intention — its conditions or its `rests_on` edges."""

    kind: str
    basis: str
    element: Literal["row", "conditions", "rests_on"] = "row"


def cites_legal(source_basis: str, target: Cited) -> bool:
    """The citation rule (graph.md «cites»): inferred and committed rows cite only
    observed or computed rows; a computed row derives from observed or
    computed rows and from an intention's conditions and rests_on edges —
    deriving from a claim or from any other intention content is refused."""
    if source_basis in ("inferred", "committed"):
        return target.element == "row" and target.basis in ("observed", "computed")
    if source_basis == "computed":
        if target.element == "row":
            return target.basis in ("observed", "computed")
        return target.kind == "intention" and target.element in (
            "conditions", "rests_on",
        )
    return False


def rests_on_legal(source_basis: str, target: Cited) -> bool:
    """A committed row may rest on rows of any basis — resting is what makes
    its reasons reconsiderable. Only a committed row rests."""
    return source_basis == "committed"
