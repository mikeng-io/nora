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
    # Without the supported-status guard, this would build a verdict with
    # removes_nothing is False despite nothing having been contradicted —
    # exactly the outcome this property exists to rule out.
    with pytest.raises(ValueError, match="supersedes"):
        _verdict(
            status="supported",
            supersedes="0191f2c4-0000-7000-8000-000000000001",
        )


def test_supported_cannot_supersede_anything():
    """A source that agrees with a claim does not thereby remove some other
    fact. `supersedes` is reachable only from `contradicted`."""
    with pytest.raises(ValueError, match="supersedes"):
        FactVerdict(
            subject="x", predicate="has", object="y", status="supported",
            addresses_claim=True, source_url="https://a.test/1",
            source_excerpt="...",
            supersedes="0191f2c4-0000-7000-8000-000000000001",
        )


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


def test_a_fact_requires_a_hex_instruction_digest():
    """The DB CHECK is `instruction_sha256 ~ '^[0-9a-f]{64}$'`; a 64-character
    string that is the right length but not hex must not slip past the
    domain only to be refused at the column."""
    with pytest.raises(ValueError, match="instruction_sha256"):
        Fact(
            id="0191f2c4-0000-7000-8000-000000000001", workspace="nora",
            subject_id="e1", predicate="has", object_entity_id="e2",
            object_literal=None,
            source_url="https://example.test/a", source_excerpt="x",
            valid_from=1, created_at=1, instruction_sha256="z" * 64,
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


def test_a_fact_cannot_be_superseded_at_without_a_successor():
    """The direction a one-directional implication would miss: `superseded_at`
    set with `superseded_by` NULL hides the row from `is_standing` with no
    record of what replaced it — half a merge record, thrown away."""
    with pytest.raises(ValueError, match="superseded"):
        Fact(
            id="0191f2c4-0000-7000-8000-000000000001", workspace="nora",
            subject_id="e1", predicate="has", object_entity_id="e2",
            object_literal=None,
            source_url="https://example.test/a", source_excerpt="x",
            valid_from=1, created_at=1, instruction_sha256="a" * 64,
            model="ollamacloud/deepseek-v4-flash", established_by="tick-1",
            superseded_at=2,
        )


def test_a_fact_cannot_name_a_successor_without_a_time():
    with pytest.raises(ValueError, match="superseded"):
        Fact(
            id="0191f2c4-0000-7000-8000-000000000001", workspace="nora",
            subject_id="e1", predicate="has", object_entity_id="e2",
            object_literal=None,
            source_url="https://example.test/a", source_excerpt="x",
            valid_from=1, created_at=1, instruction_sha256="a" * 64,
            model="ollamacloud/deepseek-v4-flash", established_by="tick-1",
            superseded_by="0191f2c4-0000-7000-8000-000000000002",
        )


def test_an_unknown_warrant_kind_is_refused():
    with pytest.raises(ValueError, match="warrant_kind"):
        Fact(
            id="0191f2c4-0000-7000-8000-000000000001", workspace="nora",
            subject_id="e1", predicate="has", object_entity_id="e2",
            object_literal=None,
            source_url="https://example.test/a", source_excerpt="x",
            valid_from=1, created_at=1, instruction_sha256="a" * 64,
            model="ollamacloud/deepseek-v4-flash", established_by="tick-1",
            warrant_kind="guess",
        )


def test_a_source_warrant_requires_a_source_url():
    """Mirrors `fact_source_url_warrant_check`: `source_url` is required
    exactly when `warrant_kind == 'source'` — the default."""
    with pytest.raises(ValueError, match="source_url"):
        Fact(
            id="0191f2c4-0000-7000-8000-000000000001", workspace="nora",
            subject_id="e1", predicate="has", object_entity_id="e2",
            object_literal=None,
            source_url=None, source_excerpt="x",
            valid_from=1, created_at=1, instruction_sha256="a" * 64,
            model="ollamacloud/deepseek-v4-flash", established_by="tick-1",
        )


def test_a_source_warrant_requires_a_source_excerpt():
    with pytest.raises(ValueError, match="source_excerpt"):
        Fact(
            id="0191f2c4-0000-7000-8000-000000000001", workspace="nora",
            subject_id="e1", predicate="has", object_entity_id="e2",
            object_literal=None,
            source_url="https://example.test/a", source_excerpt=None,
            valid_from=1, created_at=1, instruction_sha256="a" * 64,
            model="ollamacloud/deepseek-v4-flash", established_by="tick-1",
        )


def test_a_non_source_warrant_forbids_a_source_url():
    """"Forbidden otherwise", not merely optional: an observation-warranted
    fact carrying a stray `source_url` would be undefined in meaning — is it
    corroborating, decorative? — which is the exact ambiguity a discriminated
    warrant exists to kill."""
    with pytest.raises(ValueError, match="source_url"):
        Fact(
            id="0191f2c4-0000-7000-8000-000000000001", workspace="nora",
            subject_id="e1", predicate="has", object_entity_id="e2",
            object_literal=None,
            warrant_kind="observation",
            source_url="https://example.test/a", source_excerpt=None,
            valid_from=1, created_at=1, instruction_sha256="a" * 64,
            model="ollamacloud/deepseek-v4-flash", established_by="tick-1",
        )


def test_a_non_source_warrant_forbids_a_source_excerpt():
    with pytest.raises(ValueError, match="source_excerpt"):
        Fact(
            id="0191f2c4-0000-7000-8000-000000000001", workspace="nora",
            subject_id="e1", predicate="has", object_entity_id="e2",
            object_literal=None,
            warrant_kind="stated",
            source_url=None, source_excerpt="x",
            valid_from=1, created_at=1, instruction_sha256="a" * 64,
            model="ollamacloud/deepseek-v4-flash", established_by="tick-1",
        )


def test_a_premises_warrant_requires_at_least_one_premise():
    with pytest.raises(ValueError, match="premise_ids"):
        Fact(
            id="0191f2c4-0000-7000-8000-000000000001", workspace="nora",
            subject_id="e1", predicate="has", object_entity_id="e2",
            object_literal=None,
            warrant_kind="premises",
            source_url=None, source_excerpt=None, premise_ids=(),
            valid_from=1, created_at=1, instruction_sha256="a" * 64,
            model="ollamacloud/deepseek-v4-flash", established_by="tick-1",
        )


def test_a_non_premises_warrant_forbids_premise_ids():
    with pytest.raises(ValueError, match="premise_ids"):
        Fact(
            id="0191f2c4-0000-7000-8000-000000000001", workspace="nora",
            subject_id="e1", predicate="has", object_entity_id="e2",
            object_literal=None,
            warrant_kind="observation",
            source_url=None, source_excerpt=None,
            premise_ids=("0191f2c4-0000-7000-8000-000000000009",),
            valid_from=1, created_at=1, instruction_sha256="a" * 64,
            model="ollamacloud/deepseek-v4-flash", established_by="tick-1",
        )


def test_an_unknown_fidelity_is_refused():
    with pytest.raises(ValueError, match="fidelity"):
        Fact(
            id="0191f2c4-0000-7000-8000-000000000001", workspace="nora",
            subject_id="e1", predicate="has", object_entity_id="e2",
            object_literal=None,
            source_url="https://example.test/a", source_excerpt="x",
            valid_from=1, created_at=1, instruction_sha256="a" * 64,
            model="ollamacloud/deepseek-v4-flash", established_by="tick-1",
            fidelity="certain",
        )


def test_a_supersession_without_a_reason_is_refused():
    """Same biconditional shape as `superseded_at`/`superseded_by`, and for
    the same reason: the reason a fact was superseded cannot be
    reconstructed from the row afterwards at any price."""
    with pytest.raises(ValueError, match="supersede_reason"):
        Fact(
            id="0191f2c4-0000-7000-8000-000000000001", workspace="nora",
            subject_id="e1", predicate="has", object_entity_id="e2",
            object_literal=None,
            source_url="https://example.test/a", source_excerpt="x",
            valid_from=1, created_at=1, instruction_sha256="a" * 64,
            model="ollamacloud/deepseek-v4-flash", established_by="tick-1",
            superseded_at=2, superseded_by="0191f2c4-0000-7000-8000-000000000002",
        )


def test_a_reason_without_a_supersession_is_refused():
    with pytest.raises(ValueError, match="supersede_reason"):
        Fact(
            id="0191f2c4-0000-7000-8000-000000000001", workspace="nora",
            subject_id="e1", predicate="has", object_entity_id="e2",
            object_literal=None,
            source_url="https://example.test/a", source_excerpt="x",
            valid_from=1, created_at=1, instruction_sha256="a" * 64,
            model="ollamacloud/deepseek-v4-flash", established_by="tick-1",
            supersede_reason="stale",
        )


def test_an_unknown_supersede_reason_is_refused():
    with pytest.raises(ValueError, match="supersede_reason"):
        Fact(
            id="0191f2c4-0000-7000-8000-000000000001", workspace="nora",
            subject_id="e1", predicate="has", object_entity_id="e2",
            object_literal=None,
            source_url="https://example.test/a", source_excerpt="x",
            valid_from=1, created_at=1, instruction_sha256="a" * 64,
            model="ollamacloud/deepseek-v4-flash", established_by="tick-1",
            superseded_at=2, superseded_by="0191f2c4-0000-7000-8000-000000000002",
            supersede_reason="lies",
        )


def test_a_well_formed_premises_fact_is_accepted():
    """The positive case for the two new warrant kinds not warranted by a
    page: satisfiable, not just violable."""
    f = Fact(
        id="0191f2c4-0000-7000-8000-000000000001", workspace="nora",
        subject_id="e1", predicate="has", object_entity_id="e2",
        object_literal=None,
        warrant_kind="premises",
        source_url=None, source_excerpt=None,
        premise_ids=("0191f2c4-0000-7000-8000-000000000009",),
        valid_from=1, created_at=1, instruction_sha256="a" * 64,
        model="ollamacloud/deepseek-v4-flash", established_by="tick-1",
        fidelity="tentative",
    )
    assert f.warrant_kind == "premises"
    assert f.fidelity == "tentative"


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
