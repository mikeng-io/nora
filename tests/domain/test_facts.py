"""The verdict's invariants. Each test names the failure it forbids — a
verdict shape that would let a source's SILENCE remove a fact."""
from __future__ import annotations

import pytest

from nora.domain.facts import (
    Fact,
    FactCandidate,
    FactEntity,
    FactPremise,
    FactVerdict,
)


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


def test_not_covered_may_not_carry_a_source():
    """FORBIDDEN, not merely optional, and the asymmetry with the two findings
    is the point.

    A `not_covered` verdict writes nothing, so a source attached to one has no
    destination — but it does have a reader. «Here is the page, and it did not
    cover the claim» is one careless sentence away from «here is the page, and
    it says no», which is R-53's failure wearing evidence it never earned.

    `nora/dreamer/objectives/fact-adjudication.md` already tells the model
    these fields are forbidden here. Until this test, nothing enforced it: the
    instruction and the type disagreed, which is a promise no shape held in the
    one tier whose whole thesis is that the rule belongs in a shape."""
    with pytest.raises(ValueError, match="may not carry a source"):
        _verdict(
            status="not_covered", addresses_claim=False,
            source_url="https://example.com/best-tower-order",
            source_excerpt="The blue tower is the natural second.",
        )
    with pytest.raises(ValueError, match="may not carry a source"):
        _verdict(status="not_covered", addresses_claim=False, source_excerpt="x")


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
            subject_id="e1", predicate="has", object_entity_id=None,
            object_literal="a literal",
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
            subject_id="e1", predicate="has", object_entity_id=None,
            object_literal="a literal",
            source_url="https://example.test/a", source_excerpt="x",
            valid_from=1, created_at=1, instruction_sha256="z" * 64,
            model="ollamacloud/deepseek-v4-flash", established_by="tick-1",
        )


def _fact(**over):
    """A valid `Fact`. Named so the invariant under test is the only thing
    varying between the cases below."""
    kw = {
        "id": "0191f2c4-0000-7000-8000-000000000001", "workspace": "nora",
        "subject_id": "e1", "predicate": "has",
        "object_entity_id": None, "object_literal": "a literal",
        "source_url": "https://example.test/a", "source_excerpt": "x",
        "valid_from": 1, "created_at": 1, "instruction_sha256": "a" * 64,
        "model": "ollamacloud/deepseek-v4-flash", "established_by": "tick-1",
    }
    kw.update(over)
    return Fact(**kw)


def test_an_entity_id_and_a_literal_coexist():
    """Migration 0038 / `21-substrate-contract.md` §10. This was refused
    before — the exactly-one CHECK forced promotion to DESTROY the surface
    string, which broke the negative cache (dedup compares `object_literal`,
    and `NULL = 'x'` is NULL) and made two render paths print a bare uuid.
    A resolved fact now carries BOTH: the words the room used, and the entity
    they resolve to."""
    f = _fact(
        object_entity_id="0191f2c4-0000-7000-8000-0000000000e2",
        object_literal="House House",
    )
    assert f.object_literal == "House House"
    assert f.object_entity_id == "0191f2c4-0000-7000-8000-0000000000e2"


def test_object_literal_is_not_optional_on_the_type():
    """The entity id is an ADDITIVE pointer, never a replacement.

    **Asserted on the ANNOTATION, not by omitting the argument.** The first
    version of this raised `TypeError` from leaving `object_literal` out — which
    is Python's dataclass constructor refusing a missing positional, and stays
    true whether the field is `str` or `str | None`. It would have passed
    against the very reversion its docstring said it protected. The type is the
    thing that changed, so the type is the thing to read."""
    assert Fact.__annotations__["object_literal"] == "str"
    assert Fact.__annotations__["object_entity_id"] == "str | None"


def test_a_blank_literal_object_is_refused():
    """`""` and `"   "` are non-NULL to every query asking what a fact says,
    so NOT NULL alone would turn R-27's `unavailable` into a false `ok`. The
    column cannot see it; the domain can."""
    with pytest.raises(ValueError, match="object_literal"):
        _fact(object_literal="   ")


def test_a_fact_cannot_be_superseded_at_without_a_successor():
    """The direction a one-directional implication would miss: `superseded_at`
    set with `superseded_by` NULL hides the row from `is_standing` with no
    record of what replaced it — half a merge record, thrown away."""
    with pytest.raises(ValueError, match="superseded"):
        Fact(
            id="0191f2c4-0000-7000-8000-000000000001", workspace="nora",
            subject_id="e1", predicate="has", object_entity_id=None,
            object_literal="a literal",
            source_url="https://example.test/a", source_excerpt="x",
            valid_from=1, created_at=1, instruction_sha256="a" * 64,
            model="ollamacloud/deepseek-v4-flash", established_by="tick-1",
            superseded_at=2,
        )


def test_a_fact_cannot_name_a_successor_without_a_time():
    with pytest.raises(ValueError, match="superseded"):
        Fact(
            id="0191f2c4-0000-7000-8000-000000000001", workspace="nora",
            subject_id="e1", predicate="has", object_entity_id=None,
            object_literal="a literal",
            source_url="https://example.test/a", source_excerpt="x",
            valid_from=1, created_at=1, instruction_sha256="a" * 64,
            model="ollamacloud/deepseek-v4-flash", established_by="tick-1",
            superseded_by="0191f2c4-0000-7000-8000-000000000002",
        )


def test_an_unknown_warrant_kind_is_refused():
    with pytest.raises(ValueError, match="warrant_kind"):
        Fact(
            id="0191f2c4-0000-7000-8000-000000000001", workspace="nora",
            subject_id="e1", predicate="has", object_entity_id=None,
            object_literal="a literal",
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
            subject_id="e1", predicate="has", object_entity_id=None,
            object_literal="a literal",
            source_url=None, source_excerpt="x",
            valid_from=1, created_at=1, instruction_sha256="a" * 64,
            model="ollamacloud/deepseek-v4-flash", established_by="tick-1",
        )


def test_a_source_warrant_requires_a_source_excerpt():
    with pytest.raises(ValueError, match="source_excerpt"):
        Fact(
            id="0191f2c4-0000-7000-8000-000000000001", workspace="nora",
            subject_id="e1", predicate="has", object_entity_id=None,
            object_literal="a literal",
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
            subject_id="e1", predicate="has", object_entity_id=None,
            object_literal="a literal",
            warrant_kind="observation",
            source_url="https://example.test/a", source_excerpt=None,
            valid_from=1, created_at=1, instruction_sha256="a" * 64,
            model="ollamacloud/deepseek-v4-flash", established_by="tick-1",
        )


def test_a_non_source_warrant_forbids_a_source_excerpt():
    with pytest.raises(ValueError, match="source_excerpt"):
        Fact(
            id="0191f2c4-0000-7000-8000-000000000001", workspace="nora",
            subject_id="e1", predicate="has", object_entity_id=None,
            object_literal="a literal",
            warrant_kind="stated",
            source_url=None, source_excerpt="x",
            valid_from=1, created_at=1, instruction_sha256="a" * 64,
            model="ollamacloud/deepseek-v4-flash", established_by="tick-1",
        )


def test_a_premises_fact_needs_no_source_url_or_excerpt():
    """The positive case for a warrant kind not backed by a page: satisfiable,
    not just violable. Premises themselves are `FactPremise` rows, not a field
    on `Fact` (2026-08-21) — this dataclass cannot check "at least one premise
    exists" any more than the database can; see `FactPremise`'s docstring."""
    f = Fact(
        id="0191f2c4-0000-7000-8000-000000000001", workspace="nora",
        subject_id="e1", predicate="has", object_entity_id=None,
        object_literal="a literal",
        warrant_kind="premises",
        source_url=None, source_excerpt=None,
        valid_from=1, created_at=1, instruction_sha256="a" * 64,
        model="ollamacloud/deepseek-v4-flash", established_by="tick-1",
        fidelity="tentative",
    )
    assert f.warrant_kind == "premises"


def test_fidelity_defaults_to_tentative_not_proven():
    """A default is what a value gets when nobody thought about it — in a
    fail-closed codebase that value must underclaim, never overclaim. An
    omitted `fidelity` must not silently read as verified."""
    f = Fact(
        id="0191f2c4-0000-7000-8000-000000000001", workspace="nora",
        subject_id="e1", predicate="has", object_entity_id=None,
        object_literal="a literal",
        source_url="https://example.test/a", source_excerpt="x",
        valid_from=1, created_at=1, instruction_sha256="a" * 64,
        model="ollamacloud/deepseek-v4-flash", established_by="tick-1",
    )
    assert f.fidelity == "tentative"


def test_an_unknown_fidelity_is_refused():
    with pytest.raises(ValueError, match="fidelity"):
        Fact(
            id="0191f2c4-0000-7000-8000-000000000001", workspace="nora",
            subject_id="e1", predicate="has", object_entity_id=None,
            object_literal="a literal",
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
            subject_id="e1", predicate="has", object_entity_id=None,
            object_literal="a literal",
            source_url="https://example.test/a", source_excerpt="x",
            valid_from=1, created_at=1, instruction_sha256="a" * 64,
            model="ollamacloud/deepseek-v4-flash", established_by="tick-1",
            superseded_at=2, superseded_by="0191f2c4-0000-7000-8000-000000000002",
        )


def test_a_reason_without_a_supersession_is_refused():
    with pytest.raises(ValueError, match="supersede_reason"):
        Fact(
            id="0191f2c4-0000-7000-8000-000000000001", workspace="nora",
            subject_id="e1", predicate="has", object_entity_id=None,
            object_literal="a literal",
            source_url="https://example.test/a", source_excerpt="x",
            valid_from=1, created_at=1, instruction_sha256="a" * 64,
            model="ollamacloud/deepseek-v4-flash", established_by="tick-1",
            supersede_reason="stale",
        )


def test_an_unknown_supersede_reason_is_refused():
    with pytest.raises(ValueError, match="supersede_reason"):
        Fact(
            id="0191f2c4-0000-7000-8000-000000000001", workspace="nora",
            subject_id="e1", predicate="has", object_entity_id=None,
            object_literal="a literal",
            source_url="https://example.test/a", source_excerpt="x",
            valid_from=1, created_at=1, instruction_sha256="a" * 64,
            model="ollamacloud/deepseek-v4-flash", established_by="tick-1",
            superseded_at=2, superseded_by="0191f2c4-0000-7000-8000-000000000002",
            supersede_reason="lies",
        )


def test_a_fact_cannot_be_its_own_premise():
    with pytest.raises(ValueError, match="premise"):
        FactPremise(fact_id="0191f2c4-0000-7000-8000-000000000001",
                    premise_fact_id="0191f2c4-0000-7000-8000-000000000001")


def test_a_fact_premise_edge_names_two_distinct_facts():
    edge = FactPremise(fact_id="0191f2c4-0000-7000-8000-000000000001",
                       premise_fact_id="0191f2c4-0000-7000-8000-000000000002")
    assert edge.fact_id != edge.premise_fact_id


def _candidate(**overrides) -> FactCandidate:
    kwargs = {
        "id": "c1", "workspace": "nora", "subject_id": "e1", "predicate": "has",
        "object": "black_tower", "first_seen_at": 1, "last_seen_at": 2,
        "occurrence_count": 1, "observed_in": ("signal:abc",),
        "status": "pending", "established_by": "tag-1",
        "instruction_sha256": "a" * 64, "model": "ollamacloud/deepseek-v4-flash",
    }
    return FactCandidate(**{**kwargs, **overrides})


def test_candidate_and_entity_carry_their_workspace():
    c = _candidate()
    assert c.workspace == "nora"
    assert FactEntity(id="e1", workspace="nora", canonical_name="Big Walk",
                      aliases=("big walk", "大步行"), created_at=1).workspace == "nora"


def test_a_candidate_requires_a_full_instruction_digest():
    """Migration 0027 made `fact_candidate.instruction_sha256` NOT NULL with
    `fact_candidate_instruction_sha256_check`; this is the domain half, so
    `InMemoryFactCandidateStore` refuses exactly what Postgres would rather
    than accepting a row the SQL store cannot write."""
    with pytest.raises(ValueError, match="instruction_sha256"):
        _candidate(instruction_sha256="deadbeef")


def test_a_candidate_requires_a_hex_instruction_digest():
    """Right length, wrong alphabet — the case a `len(...) == 64` check would
    wave through and `~ '^[0-9a-f]{64}$'` would then refuse at the column."""
    with pytest.raises(ValueError, match="instruction_sha256"):
        _candidate(instruction_sha256="z" * 64)


def test_a_candidate_requires_a_named_model():
    """`fact_candidate_model_is_named_check` is `length(btrim(model)) > 0`:
    `'   '` is non-NULL to every query asking which rows are attributable, so
    a blank stamp is a false `ok`, not a missing value."""
    with pytest.raises(ValueError, match="model"):
        _candidate(model="   ")
