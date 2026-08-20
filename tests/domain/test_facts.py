"""The verdict's invariants. Each test names the failure it forbids — a
verdict shape that would let a source's SILENCE remove a fact."""
from __future__ import annotations

import pytest

from nora.domain.facts import Fact, FactCandidate, FactEntity, FactVerdict


def _verdict(**over):
    base = {
        "subject": "big_walk", "predicate": "has", "object": "black_tower",
        "status": "supported", "addresses_claim": True,
        "source_url": "https://keengamer.example/walkthrough",
        "source_excerpt": "work through the Red, Green, Yellow, Blue, and Black towers",
        "supersedes": None,
    }
    base.update(over)
    return FactVerdict(**base)


def test_not_covered_cannot_supersede_anything():
    """THE test. A source that does not reach the question may not remove a
    fact — and the shape, not a rule, is what forbids it."""
    with pytest.raises(ValueError, match="not_covered"):
        _verdict(status="not_covered", supersedes="0191f2c4-0000-7000-8000-000000000001")


def test_not_covered_needs_no_source():
    """«I could not check» is a real, complete verdict. It carries no warrant
    because there is nothing to warrant."""
    v = _verdict(status="not_covered", source_url=None, source_excerpt=None,
                 addresses_claim=False)
    assert v.status == "not_covered"
    assert v.removes_nothing is True


def test_contradicted_requires_the_fact_it_replaces():
    with pytest.raises(ValueError, match="supersedes"):
        _verdict(status="contradicted", supersedes=None)


def test_contradicted_requires_a_source_that_addresses_the_claim():
    """The 02:27:58 failure: a Wikipedia infobox about the release date used to
    repudiate towers. On-topic is a precondition for contradiction."""
    with pytest.raises(ValueError, match="addresses_claim"):
        _verdict(status="contradicted", addresses_claim=False,
                 supersedes="0191f2c4-0000-7000-8000-000000000001")


def test_supported_requires_a_warrant():
    with pytest.raises(ValueError, match="source"):
        _verdict(status="supported", source_url=None)


def test_an_unknown_status_is_refused():
    with pytest.raises(ValueError, match="status"):
        _verdict(status="refuted")


def test_only_contradicted_removes_anything():
    assert _verdict(status="supported").removes_nothing is True
    assert _verdict(
        status="contradicted", supersedes="0191f2c4-0000-7000-8000-000000000001"
    ).removes_nothing is False


def test_a_fact_requires_a_full_instruction_digest():
    """No legacy rows exist, so the provenance invariant is TOTAL here —
    unlike the four Dreamer tables, where it is nullable."""
    with pytest.raises(ValueError, match="instruction_sha256"):
        Fact(
            id="0191f2c4-0000-7000-8000-000000000001", workspace="nora",
            subject_id="e1", predicate="has", object_entity_id="e2",
            object_literal=None,
            source_url="https://example.test/a", source_excerpt="x",
            valid_from=1, created_at=1, instruction_sha256="deadbeef",
            model="ollamacloud/deepseek-v4-flash", established_by="tick-1",
        )


def test_a_fact_has_exactly_one_object():
    with pytest.raises(ValueError, match="object"):
        Fact(
            id="0191f2c4-0000-7000-8000-000000000001", workspace="nora",
            subject_id="e1", predicate="has", object_entity_id="e2",
            object_literal="also this",
            source_url="https://example.test/a", source_excerpt="x",
            valid_from=1, created_at=1, instruction_sha256="a" * 64,
            model="ollamacloud/deepseek-v4-flash", established_by="tick-1",
        )


def test_candidate_and_entity_carry_their_workspace():
    c = FactCandidate(
        id="c1", workspace="nora", subject_id="e1", predicate="has",
        object="black_tower", first_seen_at=1, last_seen_at=2,
        occurrence_count=1, observed_in=("signal:abc",), status="pending",
        established_by="tag-1",
    )
    assert c.workspace == "nora"
    assert FactEntity(id="e1", workspace="nora", canonical_name="Big Walk",
                      aliases=("big walk", "大步行"), created_at=1).workspace == "nora"
