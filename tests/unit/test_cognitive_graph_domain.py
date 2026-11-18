"""The Cognitive Graph's closed vocabularies and pure rules (phase 1).

The CHECK value sets in the SQL schema must match the domain's closed
vocabularies. The first tests here compare both without a database.
"""

from __future__ import annotations

import re
from typing import get_args

import sqlalchemy as sa

from nora.domain.graph_registration import SourceKind
from nora.infra.sql.cognitive_graph_schema import (
    CLAIM,
    EDGE,
    ENTITY,
    INTENTION,
    REGISTRY,
)
from nora.memory.domain import graph

TABLES = {"registry": REGISTRY, "entity": ENTITY, "claim": CLAIM,
          "intention": INTENTION, "edge": EDGE}

#: The CHECK value set each named constraint must carry, as the Literal it
#: duplicates. Constraints whose rule is a pairing or a comparison carry no
#: value set and are not listed.
CHECK_LITERALS: dict[str, object] = {
    "ck_registry_kind": graph.Kind,
    "ck_registry_state": graph.RegistryState,
    "ck_entity_basis": graph.EntityBasis,
    "ck_claim_basis": graph.ClaimBasis,
    "ck_intention_basis": graph.IntentionBasis,
    "ck_edge_basis": graph.GraphBasis,
    "ck_entity_state": graph.EntityState,
    "ck_claim_state": graph.ClaimState,
    "ck_intention_state": graph.IntentionState,
    "ck_edge_state": graph.EdgeState,
    "ck_entity_pointer_kind": graph.PointerKind,
    "ck_entity_source_ref_kind": SourceKind,
    "ck_claim_part": graph.ClaimPart,
    "ck_intention_form": graph.IntentionForm,
    "ck_intention_done_when_form": graph.DoneWhenForm,
    "ck_intention_holds_while_form": graph.ConditionForm,
    "ck_intention_drop_when_form": graph.ConditionForm,
    "ck_intention_delivery_mode": graph.DeliveryMode,
    "ck_intention_achieve_ending": graph.AchieveEnding,
    "ck_intention_maintain_ending": graph.MaintainEnding,
    "ck_edge_type": graph.EdgeType,
}
for _kind in ("entity", "claim", "intention", "edge"):
    CHECK_LITERALS[f"ck_{_kind}_exposure"] = graph.Exposure
    CHECK_LITERALS[f"ck_{_kind}_reasoning"] = graph.Reasoning
    CHECK_LITERALS[f"ck_{_kind}_confidence"] = graph.Confidence

_LITERAL_SETS = {
    frozenset(get_args(literal))
    for literal in (
        graph.Basis, graph.GraphBasis, graph.EntityBasis, graph.ClaimBasis,
        graph.IntentionBasis, graph.Kind, graph.Exposure, graph.Reasoning,
        graph.Confidence, graph.ClaimPart, graph.PointerKind,
        graph.ConditionForm, graph.DoneWhenForm, graph.IntentionForm,
        graph.AchieveEnding, graph.MaintainEnding, graph.DeliveryMode,
        graph.RegistryState, graph.EntityState, graph.ClaimState,
        graph.IntentionState, graph.EdgeState, graph.EdgeType,
        graph.EdgeFamily, SourceKind,
    )
}
_IN = re.compile(r"\bIN\s*\(([^)]*)\)")


def _in_sets(constraint: sa.CheckConstraint) -> list[frozenset[str]]:
    return [
        frozenset(v.strip().strip("'") for v in group.split(","))
        for group in _IN.findall(str(constraint.sqltext))
    ]


def test_every_check_value_set_is_a_declared_literal() -> None:
    seen: dict[str, frozenset[str]] = {}
    for table in TABLES.values():
        for constraint in table.constraints:
            if isinstance(constraint, sa.CheckConstraint):
                for values in _in_sets(constraint):
                    seen[constraint.name] = seen.get(constraint.name, frozenset()) | values
    for name, values in seen.items():
        assert values in _LITERAL_SETS, f"{name}: {sorted(values)}"


def test_every_declared_check_set_equals_its_literal() -> None:
    checks: dict[str, frozenset[str]] = {}
    for table in TABLES.values():
        for constraint in table.constraints:
            if isinstance(constraint, sa.CheckConstraint):
                sets = _in_sets(constraint)
                if sets:
                    checks[constraint.name] = frozenset().union(*sets)
    assert set(CHECK_LITERALS) <= set(checks)
    for name, literal in CHECK_LITERALS.items():
        assert checks[name] == frozenset(get_args(literal)), name


def test_the_edge_vocabulary_is_the_closed_set() -> None:
    assert set(graph.EDGES) == frozenset(get_args(graph.EdgeType))
    assert all(
        declaration.family in get_args(graph.EdgeFamily)
        for declaration in graph.EDGES.values()
    )


def test_every_edge_type_declares_endpoints_and_bases() -> None:
    for name, declaration in graph.EDGES.items():
        assert declaration.source_kinds <= set(get_args(graph.Kind)), name
        assert declaration.target_kinds <= set(get_args(graph.Kind)), name
        assert declaration.bases <= set(get_args(graph.GraphBasis)), name


def test_endpoints_legal() -> None:
    assert graph.endpoints_legal("cites", "claim", "item")
    assert graph.endpoints_legal("cites", "aggregate", "entity")
    assert graph.endpoints_legal("about", "claim", "entity")
    assert graph.endpoints_legal("draws_on", "claim", "intention")
    assert graph.endpoints_legal("rests_on", "intention", "claim")
    assert graph.endpoints_legal("serves", "intention", "intention")
    assert graph.endpoints_legal("watches", "intention", "entity")
    assert graph.endpoints_legal("closed_by", "intention", "item")
    assert graph.endpoints_legal("interested_in", "entity", "entity")
    assert not graph.endpoints_legal("cites", "item", "claim")
    assert not graph.endpoints_legal("about", "entity", "claim")
    assert not graph.endpoints_legal("rests_on", "claim", "intention")
    assert not graph.endpoints_legal("serves", "claim", "intention")
    assert not graph.endpoints_legal("closed_by", "intention", "entity")
    assert not graph.endpoints_legal("likes", "entity", "entity")


def test_basis_legal() -> None:
    assert graph.basis_legal("cites", "inferred")
    assert graph.basis_legal("rests_on", "committed")
    assert graph.basis_legal("about", "computed")
    assert graph.basis_legal("closed_by", "computed")
    assert not graph.basis_legal("rests_on", "inferred")
    assert not graph.basis_legal("about", "inferred")
    assert not graph.basis_legal("cites", "observed")
    assert not graph.basis_legal("closed_by", "committed")


def test_only_cites_is_support() -> None:
    assert graph.is_support("cites")
    for edge_type in get_args(graph.EdgeType):
        if edge_type != "cites":
            assert not graph.is_support(edge_type)


def test_cites_legal() -> None:
    observed = graph.Cited(kind="item", basis="observed")
    computed = graph.Cited(kind="aggregate", basis="computed")
    claim = graph.Cited(kind="claim", basis="inferred")
    intention = graph.Cited(kind="intention", basis="committed")
    assert graph.cites_legal("inferred", observed)
    assert graph.cites_legal("inferred", computed)
    assert not graph.cites_legal("inferred", claim)
    assert not graph.cites_legal("inferred", intention)
    assert not graph.cites_legal(
        "inferred", graph.Cited("intention", "committed", "conditions"),
    )
    assert graph.cites_legal("computed", observed)
    assert graph.cites_legal("computed", computed)
    assert not graph.cites_legal("computed", claim)
    assert not graph.cites_legal("computed", intention)
    assert graph.cites_legal(
        "computed", graph.Cited("intention", "committed", "conditions"),
    )
    assert graph.cites_legal(
        "computed", graph.Cited("intention", "committed", "rests_on"),
    )
    assert not graph.cites_legal(
        "computed", graph.Cited("claim", "inferred", "conditions"),
    )
    assert graph.cites_legal("committed", observed)
    assert graph.cites_legal("committed", computed)
    assert not graph.cites_legal("committed", claim)
    assert not graph.cites_legal("committed", intention)
    assert not graph.cites_legal("observed", observed)


def test_rests_on_legal() -> None:
    for basis in get_args(graph.Basis):
        assert graph.rests_on_legal("committed", graph.Cited("claim", basis))
    assert not graph.rests_on_legal("inferred", graph.Cited("item", "observed"))
    assert not graph.rests_on_legal("computed", graph.Cited("item", "observed"))
