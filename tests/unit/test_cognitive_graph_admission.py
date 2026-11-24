"""Form and scope of model proposals, using generated rows."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast, get_args

import pytest

from nora.domain.lineage import ROW, Lineage, message
from nora.memory.domain.admission import (
    Admitted,
    FormRefusal,
    Refusal,
    RefusalReason,
    admit_batch,
)
from nora.memory.domain.admission_proposals import (
    AddEdge,
    Condition,
    CreateClaim,
    CreateEntity,
    CreateIntention,
    Link,
    MergeEntities,
    Occasion,
    PassContext,
    PersonAuthor,
    Proposal,
    ResolvedReferences,
    Retire,
    Revise,
    RoomReference,
    RowFacts,
    Scope,
    SourceReference,
)

PASS = PassContext(
    "primary",
    "group",
    False,
    "wake",
    "admission",
    "pass-1",
    "trace-1",
    "model-1",
    "code",
    "instruction",
    "skill",
    "config",
    "identity",
    "policy",
)
REFS = ResolvedReferences(
    {"group": RoomReference("group", "primary"), "dm": RoomReference("dm", "primary", True)},
    {"author": "person-a", "nora": "nora", "restricted": "person-b"},
)
SCOPE = Scope("primary")
ITEM = RowFacts(
    "item",
    "item",
    "observed",
    "live",
    "primary",
    origin_room="group",
    source="messages",
    author=PersonAuthor("person-a"),
)
AGG = RowFacts(
    "aggregate",
    "aggregate",
    "computed",
    "live",
    "primary",
    source="interval",
    dependencies=("item",),
)
CLAIM = RowFacts("old-claim", "claim", "inferred", "live", "primary")
INTENTION = RowFacts("old-intention", "intention", "committed", "open", "primary", form="achieve")
ENTITY = RowFacts("old-entity", "entity", "inferred", "admitted", "primary")
SURVIVOR = replace(ENTITY, id="survivor")
EDGE = RowFacts("old-edge", "edge", "committed", "live", "primary")
PRIMITIVES = (ITEM, AGG, CLAIM, INTENTION, ENTITY, SURVIVOR, EDGE)
NEW_CLAIM = CreateClaim(
    id="new",
    basis="inferred",
    scope=SCOPE,
    reasoning="deduced",
    confidence="high",
    citations=("item",),
    content={"text": "a fact"},
)
NEW_INTENTION = CreateIntention(
    id="new",
    basis="committed",
    scope=SCOPE,
    owner="nora",
    owed_to="author",
    form="achieve",
    condition=Condition("person_event", {"test": "a response"}),
)
NEW_ENTITY = CreateEntity(
    id="new",
    basis="inferred",
    scope=SCOPE,
    reasoning="deduced",
    confidence="high",
    citations=("item",),
    name="a topic",
)
NEW_EDGE = AddEdge(
    id="new", basis="committed", scope=SCOPE, type="rests_on", source=INTENTION.id, target=CLAIM.id
)
MERGE = MergeEntities(
    id="merge",
    basis="inferred",
    scope=SCOPE,
    reasoning="deduced",
    confidence="medium",
    citations=("item",),
    source=ENTITY.id,
    survivor=SURVIVOR.id,
)
ENTITY_LINK = Link(
    type="about",
    basis="inferred",
    source="claim-about-topic",
    target=NEW_ENTITY.id,
    reasoning="deduced",
    confidence="low",
)
TOPIC_CLAIM = replace(NEW_CLAIM, id=ENTITY_LINK.source, links=(ENTITY_LINK,))


def _batch(
    *proposals: Proposal,
    extra: tuple[RowFacts, ...] = (),
    hidden: tuple[str, ...] = (),
    context: PassContext = PASS,
) -> tuple[Admitted | Refusal, ...]:
    rows = {row.id: row for row in (*PRIMITIVES, *extra)}
    return admit_batch(proposals, context, rows.keys() - set(hidden), rows, REFS)


def _one(proposal: Proposal, **kwargs) -> Admitted | Refusal:
    return _batch(proposal, **kwargs)[0]


def test_claim_cites_only_shown_observed_or_computed_evidence() -> None:
    accepted = _one(NEW_CLAIM)
    assert isinstance(accepted, Admitted)
    assert tuple((link.type, link.target) for link in accepted.links) == (("cites", ITEM.id),)
    assert _one(NEW_CLAIM, hidden=(ITEM.id,)) == Refusal("citation_not_shown", "citations")
    for row in (CLAIM, INTENTION):
        assert _one(replace(NEW_CLAIM, citations=(row.id,))) == Refusal(
            "unrooted_citation",
            "citations",
        )


def test_citation_chain_checks_each_reachable_dependency() -> None:
    proposal = replace(NEW_CLAIM, citations=(AGG.id,))
    assert isinstance(_one(proposal, hidden=(ITEM.id,)), Admitted)
    assert _one(proposal, extra=(replace(AGG, dependencies=(CLAIM.id,)),)) == Refusal(
        "unrooted_citation",
        "citations",
    )


def test_about_and_draws_on_supply_no_evidence() -> None:
    links = (
        Link(type="about", basis="computed", source="new", target=ENTITY.id),
        Link(
            type="draws_on",
            basis="inferred",
            source="new",
            target=CLAIM.id,
            reasoning="deduced",
            confidence="high",
        ),
    )
    assert _one(replace(NEW_CLAIM, citations=(), links=links)) == Refusal(
        "missing_evidence",
        "citations",
    )
    result = _one(replace(NEW_CLAIM, links=links[1:]))
    assert isinstance(result, Admitted)
    assert {link.type for link in result.links} == {"draws_on", "cites"}


def test_inferred_rows_require_representable_reasoning_and_confidence() -> None:
    assert isinstance(_one(NEW_CLAIM), Admitted)
    for value in (None, "unrecognized"):
        assert _one(replace(NEW_CLAIM, reasoning=value)) == Refusal(
            "invalid_reasoning",
            "reasoning",
        )
        assert _one(replace(NEW_CLAIM, confidence=value)) == Refusal(
            "invalid_confidence",
            "confidence",
        )


def test_told_requires_a_person_authored_message_edit_or_reaction() -> None:
    proposal = replace(NEW_CLAIM, reasoning="told")
    for source in ("messages", "message_edit", "message_reaction"):
        assert isinstance(_one(proposal, extra=(replace(ITEM, source=source),)), Admitted)
    for author in ("nora", None):
        assert _one(proposal, extra=(replace(ITEM, author=author),)) == Refusal(
            "told_without_person",
            "citations",
        )
    assert _one(proposal, extra=(replace(ITEM, source="fetched_pages"),)) == Refusal(
        "told_without_person",
        "citations",
    )


def test_edges_require_declared_types_endpoints_and_bases() -> None:
    assert isinstance(_one(NEW_EDGE), Admitted)
    assert _one(replace(NEW_EDGE, type="imagines")) == Refusal("unknown_edge_type", "type")
    assert _one(replace(NEW_EDGE, target=INTENTION.id)) == Refusal("illegal_endpoints", "target")
    assert _one(replace(NEW_EDGE, basis="inferred", reasoning="deduced", confidence="high")) == (
        Refusal("illegal_basis", "basis")
    )


@pytest.mark.parametrize(
    ("base", "replacement", "terminal"),
    [
        (CLAIM, NEW_CLAIM, "retired"),
        (INTENTION, NEW_INTENTION, "ended"),
        (ENTITY, NEW_ENTITY, "retired"),
        (EDGE, NEW_EDGE, "retired"),
    ],
)
def test_revision_obeys_the_kind_lifecycle(base, replacement, terminal) -> None:
    proposal = Revise(base=base.id, replacement=replacement)
    siblings = (TOPIC_CLAIM,) if isinstance(replacement, CreateEntity) else ()
    assert isinstance(_batch(proposal, *siblings)[0], Admitted)
    assert _one(proposal, extra=(replace(base, state=terminal),)) == Refusal(
        "illegal_transition",
        "base",
    )


def test_intention_endings_follow_its_form() -> None:
    retire = Retire(id="end", base=INTENTION.id, basis="committed", scope=SCOPE, ending="achieved")
    accepted = _one(retire)
    assert isinstance(accepted, Admitted) and accepted.row.state == "ended"
    assert _one(replace(retire, ending="ceased")) == Refusal("illegal_transition", "ending")
    maintain = replace(INTENTION, form="maintain")
    assert isinstance(_one(replace(retire, ending="ceased"), extra=(maintain,)), Admitted)
    assert _one(retire, extra=(maintain,)) == Refusal("illegal_transition", "ending")


def test_trigger_move_requires_a_citation_but_unchanged_trigger_does_not() -> None:
    base = replace(INTENTION, trigger={"at": 1}, delivery_mode="choose_moment")
    changed = replace(NEW_INTENTION, trigger={"at": 2}, delivery_mode="choose_moment")
    proposal = Revise(base=base.id, replacement=changed)
    assert _one(proposal, extra=(base,)) == Refusal("trigger_without_evidence", "citations")
    with_evidence = replace(proposal, replacement=replace(changed, citations=(ITEM.id,)))
    decision = _one(with_evidence, extra=(base,))
    assert isinstance(decision, Admitted)
    assert tuple((link.type, link.basis) for link in decision.links) == (("cites", "committed"),)
    unchanged = replace(proposal, replacement=replace(changed, trigger=base.trigger))
    assert isinstance(_one(unchanged, extra=(base,)), Admitted)


@pytest.mark.parametrize(
    "occasion", ["called", "ambient", "proactive", "debrief", "wake", "review"]
)
def test_pass_occasion_limits_proposed_bases(occasion: Occasion) -> None:
    claim, intention = _batch(
        NEW_CLAIM, replace(NEW_INTENTION, id="intend"), context=replace(PASS, occasion=occasion)
    )
    if occasion == "wake":
        assert isinstance(claim, Admitted)
    elif occasion == "review":
        assert claim == Refusal("occasion_part", "part")
    else:
        assert claim == Refusal("occasion_basis", "basis")
    if occasion == "review":
        assert intention == Refusal("occasion_basis", "basis")
    else:
        assert isinstance(intention, Admitted)


def test_unit_is_the_pass_unit() -> None:
    accept = _one(NEW_CLAIM)
    assert isinstance(accept, Admitted) and accept.row.unit == PASS.unit
    assert _one(replace(NEW_CLAIM, scope=Scope("affiliate"))) == Refusal("wrong_unit", "unit")


def test_scope_narrows_every_exclusion_and_exposure_and_retains_lineage() -> None:
    lineage = Lineage.of({ROW: (message("group"),)})
    input_row = replace(ITEM, exposure="internal", excluded_persons=("person-b",), lineage=lineage)
    narrowed = replace(SCOPE, exposure="operator", excluded_persons=("restricted",))
    decision = _one(replace(NEW_CLAIM, scope=narrowed), extra=(input_row,))
    assert isinstance(decision, Admitted)
    assert (decision.row.exposure, decision.row.excluded_persons, decision.row.lineage) == (
        "operator",
        ("person-b",),
        lineage,
    )
    wide_exposure = replace(narrowed, exposure="external")
    assert _one(replace(NEW_CLAIM, scope=wide_exposure), extra=(input_row,)) == Refusal(
        "scope_widening",
        "exposure",
    )
    wide_exclusions = replace(narrowed, excluded_persons=())
    assert _one(replace(NEW_CLAIM, scope=wide_exclusions), extra=(input_row,)) == Refusal(
        "scope_widening",
        "excluded_persons",
    )


def test_dm_citation_marks_origin_and_refuses_affiliate_unit() -> None:
    item = replace(ITEM, origin_room="dm", room_is_dm=True)
    decision = _one(NEW_CLAIM, extra=(item,))
    assert isinstance(decision, Admitted)
    assert (decision.row.origin_room, decision.row.room_is_dm) == ("dm", True)
    assert _one(
        replace(NEW_CLAIM, scope=Scope("affiliate")),
        extra=(item,),
        context=replace(PASS, unit="affiliate"),
    ) == Refusal("wrong_unit", "citations")


def test_named_origin_must_resolve_and_unknown_person_stays_unresolved() -> None:
    scope = replace(SCOPE, origin_room="dm", persons=("author", "unknown"))
    decision = _one(replace(NEW_CLAIM, scope=scope))
    assert isinstance(decision, Admitted)
    assert (decision.row.origin_room, decision.row.persons) == ("dm", ("person-a", None))
    assert _one(replace(NEW_CLAIM, scope=replace(scope, origin_room="typo"))) == Refusal(
        "unresolved_room",
        "origin_room",
    )
    assert _one(replace(NEW_CLAIM, scope=replace(scope, excluded_persons=("typo",)))) == Refusal(
        "unresolved_person",
        "excluded_persons",
    )


@pytest.mark.parametrize("pointer", ["person", "room", "unit", "nora"])
def test_model_cannot_create_revise_merge_or_retire_pointer_entities(pointer: str) -> None:
    pointer_row = replace(ENTITY, pointer_kind=pointer, basis="computed")
    create = replace(NEW_ENTITY, pointer_kind=pointer, pointer_ref="identity")
    retire = Retire(
        id="retire",
        basis="inferred",
        scope=SCOPE,
        reasoning="deduced",
        confidence="high",
        base=ENTITY.id,
    )
    assert _one(create) == Refusal("pointer_entity", "pointer_kind")
    assert _one(Revise(base=ENTITY.id, replacement=NEW_ENTITY), extra=(pointer_row,)) == Refusal(
        "pointer_entity",
        "base",
    )
    assert _one(retire, extra=(pointer_row,)) == Refusal("pointer_entity", "base")
    assert _one(MERGE, extra=(pointer_row,)) == Refusal("pointer_entity", "source")


def test_inferred_entity_requires_an_admitted_evidenced_edge_in_its_proposal() -> None:
    assert _one(NEW_ENTITY) == Refusal("orphan_entity", "links")
    orphan, refused = _batch(NEW_ENTITY, replace(TOPIC_CLAIM, citations=()))
    assert orphan == Refusal("orphan_entity", "links")
    assert refused == Refusal("missing_evidence", "citations")
    bad_link = replace(ENTITY_LINK, source=NEW_ENTITY.id, target=CLAIM.id)
    assert _one(replace(NEW_ENTITY, links=(bad_link,))) == Refusal("illegal_endpoints", "target")
    entity, claim = _batch(NEW_ENTITY, TOPIC_CLAIM)
    assert isinstance(claim, Admitted) and claim.links[0] == ENTITY_LINK
    assert isinstance(entity, Admitted) and claim.topics == (entity,)


def test_merge_is_an_evidenced_relation_and_preserves_both_revision_chains() -> None:
    first = replace(NEW_ENTITY, id="rev-source")
    second = replace(NEW_ENTITY, id="rev-survivor")
    supporter = replace(TOPIC_CLAIM, links=tuple(
        replace(ENTITY_LINK, target=entry.id) for entry in (first, second)
    ))
    merge, rev_source, rev_survivor, _ = _batch(
        MERGE,
        Revise(base=ENTITY.id, replacement=first),
        Revise(base=SURVIVOR.id, replacement=second),
        supporter,
    )
    assert isinstance(merge, Admitted)
    assert tuple((link.type, link.source, link.target, link.basis) for link in merge.links) == (
        ("merged_into", ENTITY.id, SURVIVOR.id, "inferred"),
        ("cites", ENTITY.id, ITEM.id, "inferred"),
    )
    assert isinstance(rev_source, Admitted) and rev_source.base is not None
    assert (rev_source.base.id, rev_source.base.state) == (ENTITY.id, "merged")
    assert isinstance(rev_survivor, Admitted) and rev_survivor.base == SURVIVOR
    assert _one(replace(MERGE, citations=())) == Refusal("invalid_merge", "citations")
    assert _one(replace(MERGE, survivor=ENTITY.id)) == Refusal("invalid_merge", "survivor")
    for state in ("merged", "retired"):
        assert _one(MERGE, extra=(replace(SURVIVOR, state=state),)) == Refusal(
            "invalid_merge",
            "survivor",
        )
    assert _one(MERGE, extra=(replace(SURVIVOR, unit="affiliate"),)) == Refusal(
        "wrong_unit",
        "survivor",
    )


def test_only_earlier_admitted_rows_join_the_shown_set() -> None:
    next_edge = replace(NEW_EDGE, id="dependent", target=NEW_CLAIM.id)
    accepted, dependent = _batch(NEW_CLAIM, next_edge)
    assert isinstance(accepted, Admitted) and isinstance(dependent, Admitted)
    refused, missing = _batch(replace(NEW_CLAIM, citations=()), next_edge)
    assert refused == Refusal("missing_evidence", "citations")
    assert missing == Refusal("illegal_endpoints", "target")


def test_refusal_literal_covers_all_reachable_form_and_write_reasons() -> None:
    nora = replace(ENTITY, basis="computed", pointer_kind="nora", pointer_ref="nora")
    correction = replace(ITEM, aimed_at_nora=True)
    lesson = replace(
        NEW_CLAIM, part="lesson", content="Check the plan before committing.",
        links=(replace(ENTITY_LINK, source=NEW_CLAIM.id, target=nora.id),),
    )
    now = datetime(2026, 11, 1, tzinfo=UTC)
    guess = replace(CLAIM, part="guess", asserted_at=now, window_end=now + timedelta(hours=1))
    grade = replace(
        NEW_CLAIM, part="grade", grade="confirmed",
        links=(replace(ENTITY_LINK, source=NEW_CLAIM.id, type="grades", target=guess.id),),
    )
    review = replace(PASS, occasion="review")
    grade_cases = {"context": review, "extra": (guess, replace(ITEM, evidence_at=now))}
    nora_evidence = replace(ITEM, evidence_at=now, author="nora")
    cases = (
        (Revise(base=CLAIM.id, replacement=NEW_CLAIM), {"extra": (replace(CLAIM, part="grade"),)}),
        (replace(grade, links=()), grade_cases),
        (replace(grade, grade="holds"), grade_cases),
        (grade, {"context": review, "extra": (guess,)}),
        (grade, {"context": review, "extra": (guess, nora_evidence)}),
        (replace(lesson, links=()), {}),
        (lesson, {"extra": (nora,)}),
        (lesson, {"extra": (nora, replace(ITEM, author="nora"))}),
        (replace(lesson, content="Short"), {"extra": (nora, correction)}),
        (replace(lesson, content="Long " * 90), {"extra": (nora, correction)}),
        (cast(Proposal, object()), {}),
        (replace(NEW_EDGE, type="invalid"), {}),
        (replace(NEW_EDGE, target=INTENTION.id), {}),
        (replace(NEW_EDGE, basis="invalid"), {}),
        (Revise(base="invalid", replacement=NEW_CLAIM), {}),
        (NEW_CLAIM, {"hidden": (ITEM.id,)}),
        (replace(NEW_CLAIM, citations=(CLAIM.id,)), {}),
        (replace(NEW_CLAIM, citations=()), {}),
        (replace(NEW_CLAIM, reasoning=None), {}),
        (replace(NEW_CLAIM, confidence=None), {}),
        (replace(NEW_CLAIM, reasoning="told"), {"extra": (replace(ITEM, author=None),)}),
        (replace(NEW_CLAIM, scope=Scope("affiliate")), {}),
        (NEW_CLAIM, {"extra": (replace(ITEM, exposure="internal"),)}),
        (replace(NEW_CLAIM, scope=Scope("primary", "invalid")), {}),
        (replace(NEW_INTENTION, owner="invalid"), {}),
        (replace(NEW_ENTITY, pointer_kind="person"), {}),
        (NEW_ENTITY, {}),
        (replace(MERGE, citations=()), {}),
        (
            Revise(
                base=INTENTION.id,
                replacement=replace(
                    NEW_INTENTION, delivery_mode="choose_moment", trigger={"at": 2}
                ),
            ),
            {},
        ),
        (NEW_CLAIM, {"context": replace(PASS, occasion="called")}),
        (replace(NEW_CLAIM, id=""), {}),
        (replace(NEW_ENTITY, source_ref=SourceReference("page", "https://example.invalid")), {}),
        (
            replace(NEW_EDGE, type="watches", target=ENTITY.id),
            {"extra": (replace(INTENTION, form="maintain"),)},
        ),
    )
    decisions = [_one(proposal, **kwargs) for proposal, kwargs in cases]
    assert all(isinstance(result, Refusal) and result.field for result in decisions)
    observed = {result.reason for result in decisions if isinstance(result, Refusal)}
    assert observed == set(get_args(FormRefusal))
    # The remaining outcomes depend on the write path, exercised in infra.
    assert set(get_args(RefusalReason)) == observed | {
        "stale_base",
        "credential",
        "doctrine",
        "content",
        "unsafe_page_reference",
        "duplicate_delivery",
        "not_open",
        "witness_not_met",
        "untyped_condition",
        "not_this_delivery",
        "passive_commit_only",
        "citation_outside_pass",
        "not_a_commit",
        "room_busy",
        "echoes_doctrine",
        "echoes_lesson",
        "cap",
        "relearn",
        "loosens_law",
        "check_unavailable",
        "occasion_part",
        "already_graded",
    }
