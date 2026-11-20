"""Export refuses authority shapes absent from the selected file topology."""

import json

import pytest

from nora.domain.managed import OutcomeStatus
from nora.domain.management import Scope, ScopeKind
from tests.unit.test_managed_export import ROOM_A, _seed
from tests.unit.test_managed_export import graph as graph


@pytest.mark.parametrize("namespace,scope,relation,subject", [
    ("Room", ROOM_A.id, "unit", ("subjectset", "Unit", "generated-foreign-unit")),
    ("Unit", "family", "workspace", ("subjectset", "Workspace", "generated-foreign-workspace")),
    ("Workspace", "default", "member", ("subjectset", "Unit", "generated-foreign-unit")),
    ("Room", ROOM_A.id, "admin", ("subjectset", "Unit", "generated-foreign-unit")),
])
async def test_export_refuses_foreign_containment_and_subject_sets(
    graph, namespace, scope, relation, subject,
):
    _seed(graph)
    _, backend, _, policy, export = graph
    assert (await export.export(policy)).outcome.status is OutcomeStatus.COMPLETE
    backend.tuples.add((namespace, scope, relation, subject))
    refused = await export.export(policy)
    assert refused.outcome.status is OutcomeStatus.DENIED
    assert refused.bundle is None
    assert any(r.code == "unfaithful:subject-set-relation" for r in refused.refusals)


async def test_export_preserves_configured_containment_with_the_same_grants(graph):
    _seed(graph)
    _, backend, _, policy, export = graph
    initial = await export.export(policy)
    assert initial.outcome.status is OutcomeStatus.COMPLETE
    assert (await backend.publish_containment(scope=ROOM_A,
        parent=Scope(ScopeKind.UNIT, "family"))).status is OutcomeStatus.COMPLETE
    assert (await backend.publish_containment(scope=Scope(ScopeKind.UNIT, "family"),
        parent=Scope(ScopeKind.WORKSPACE, "home"))).status is OutcomeStatus.COMPLETE
    report = await export.export(policy)
    assert report.outcome.status is OutcomeStatus.COMPLETE
    assert report.bundle.identity_toml == initial.bundle.identity_toml
    assert report.bundle.policy_toml == initial.bundle.policy_toml
    assert report.counts["folded_relations"] == initial.counts["folded_relations"] + 2
    folded = json.loads(report.bundle.manifest_json)["folded_relations"]
    assert {"scope": "room:" + ROOM_A.id, "relation": "unit", "subject": "unit:family"} in folded
    assert {"scope": "unit:family", "relation": "workspace", "subject": "workspace:home"} in folded


async def test_an_unreadable_typed_authority_never_looks_empty_or_exposes_details(graph):
    _seed(graph)
    _, backend, _, policy, export = graph
    assert (await export.export(policy)).outcome.status is OutcomeStatus.COMPLETE
    backend.tuples.add(("Room", ROOM_A.id, "unit",
                        ("subjectset", "generated-private-namespace", "generated-private-id")))
    report = await export.export(policy)
    assert report.outcome.status is OutcomeStatus.UNAVAILABLE
    assert report.outcome.detail == "authority-read" and report.bundle is None
