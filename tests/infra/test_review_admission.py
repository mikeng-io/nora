"""Exercise grade admission against the registered evidence and tally."""

from dataclasses import replace
from datetime import timedelta

import pytest
import sqlalchemy as sa

from nora.infra.sql.cognitive_graph_schema import CLAIM, EDGE, LESSON_TALLY, REGISTRY
from nora.infra.sql.schema import SOURCE_HEALTH
from nora.memory import WakeInput, wire
from nora.memory.app.admission import Stored
from nora.memory.domain.admission import Refusal
from nora.memory.domain.admission_proposals import CreateClaim, Link, Retire, Revise, Scope
from tests.infra.test_graph_propose import BASIS, WAKE
from tests.infra.test_intention_verdict import _tick
from tests.infra.test_lesson_tally import ended, tally_rows
from tests.infra.test_pass_anchors import NOW, SCOPE
from tests.infra.test_pass_assembly import _assembler
from tests.infra.test_retrieval_scope import AT, UNIT, write_edge
from tests.infra.test_retrieval_scope import scenario as scenario
from tests.infra.test_room_busy import service, slots

REVIEW = replace(WAKE, occasion="review", model="generated-judge")
START = AT - timedelta(minutes=15)


async def guess(scenario, *, state="live"):
    await scenario.claim(
        "guess", part="guess", prediction="Generated prediction", window_end=AT, asserted_at=START,
        state=state,
    )


async def health_sample(scenario, outcome):
    async with scenario.engine.begin() as conn:
        await conn.execute(sa.insert(REGISTRY).values(id="health", kind="item", unit=UNIT))
        await conn.execute(sa.insert(SOURCE_HEALTH).values(
            graph_id="health", unit=UNIT, source="signal", event="transition", outcome=outcome,
            at_ms=int(AT.timestamp() * 1000), asserted_at=AT, occurred_at=AT,
        ))


async def changed_tally(scenario, leak: str):
    await ended(scenario, leak, "leaked", lesson="lesson")
    await _tick(scenario.engine, NOW)
    return (await tally_rows(scenario))["lesson"]


async def lesson(scenario):
    await scenario.claim("lesson", part="lesson", asserted_at=START)
    return await changed_tally(scenario, "first-leak")


def proposal(value, target, citations, *, id_="grade"):
    return CreateClaim(
        **BASIS, id=id_, scope=Scope(UNIT, exposure=None), basis="inferred", content="Assessment",
        part="grade", grade=value, citations=citations,
        links=(Link(
            type="grades", source=id_, target=target, basis="inferred", **BASIS,
        ),),
    )


async def submit(scenario, operation):
    [outcome] = (await service(scenario, slots(scenario)).admit(
        (operation,), REVIEW, (*operation.citations, *(link.target for link in operation.links)),
    )).outcomes
    return outcome


async def grade_rows(scenario):
    async with scenario.engine.connect() as conn:
        return (await conn.execute(sa.select(CLAIM.c.id, CLAIM.c.grade, CLAIM.c.model).where(
            CLAIM.c.part == "grade",
        ).order_by(CLAIM.c.id))).all()


@pytest.mark.parametrize("outcome", ("confirmed", "refuted", "expired", "holds", "weakened"))
async def test_grade_claim_records_outcome_and_cites_later_evidence(scenario, outcome):
    if outcome in ("holds", "weakened"):
        target, citation = "lesson", (await lesson(scenario))["graph_id"]
    else:
        target = "guess"
        await guess(scenario, state="awaiting_weighing")
        if outcome == "expired":
            await health_sample(scenario, "up")
            citation = "health"
        else:
            await scenario.item("later", at=AT)
            citation = "later"
    assert await submit(scenario, proposal(outcome, target, (citation,))) == Stored("grade")
    assert await grade_rows(scenario) == [("grade", outcome, REVIEW.model)]
    async with scenario.engine.connect() as conn:
        links = (await conn.execute(sa.select(EDGE.c.type, EDGE.c.dst_id).where(
            EDGE.c.src_id == "grade",
        ).order_by(EDGE.c.type))).all()
    assert links == [("cites", citation), ("grades", target)]


@pytest.mark.parametrize(("target", "outcome", "at", "nora", "reason", "field"), (
    ("missing", "confirmed", AT, False, "grade_target", "links"),
    ("guess", "holds", AT, False, "grade_outside_part", "grade"),
    ("guess", "refuted", START - timedelta(seconds=1), False, "no_later_evidence", "citations"),
    ("guess", "confirmed", AT, True, "self_assent", "citations"),
))
async def test_review_refuses_an_invalid_grade(scenario, target, outcome, at, nora, reason, field):
    await guess(scenario)
    await scenario.item("evidence", at=at, is_nora=int(nora))
    operation = proposal(outcome, target, ("evidence",))
    assert await submit(scenario, operation) == Refusal(reason, field)
    assert await grade_rows(scenario) == []


async def test_expired_grade_refuses_an_unhealthy_source(scenario):
    await guess(scenario)
    await health_sample(scenario, "down")
    assert await submit(scenario, proposal("expired", "guess", ("health",))) == Refusal(
        "no_later_evidence", "citations",
    )
    assert await grade_rows(scenario) == []


async def test_guess_with_a_live_grade_is_refused(scenario):
    await guess(scenario)
    await scenario.item("later", at=AT)
    first = proposal("refuted", "guess", ("later",))
    assert await submit(scenario, first) == Stored("grade")
    repeat = proposal("confirmed", "guess", ("later",), id_="second")
    assert await submit(scenario, repeat) == Refusal("already_graded", "links")
    assert await grade_rows(scenario) == [("grade", "refuted", REVIEW.model)]


@pytest.mark.parametrize("occasion", ("wake", "wake_history"))
@pytest.mark.parametrize("operation", ("revise", "retire"))
async def test_wake_cannot_change_a_judges_grade(scenario, occasion, operation):
    await guess(scenario)
    await scenario.entity("nora-profile", pointer_kind="nora", pointer_ref="nora")
    await write_edge(scenario, "guess", "nora-profile", "about")
    await scenario.item("later")
    assert await submit(scenario, proposal("refuted", "guess", ("later",))) == Stored("grade")
    now = await scenario.now()
    context = replace(WAKE, occasion=occasion, clock=now if occasion == "wake_history" else None)
    fields = dict(id="writer-change", scope=Scope(UNIT, exposure=None), basis="inferred",
                  citations=("later",), **BASIS)
    mutation = (Revise(base="grade", replacement=CreateClaim(**fields, content="Generated change"))
                if operation == "revise" else Retire(base="grade", **fields))
    [outcome] = (await service(scenario, slots(scenario)).admit(
        (mutation,), context, ("grade", "later"),
    )).outcomes
    assert outcome == Refusal("grade_owned_by_judge", "base")
    assembled = await _assembler(scenario).assemble("wake", WakeInput(()), (), SCOPE, now)
    profile = next(block for block in assembled.blocks if block.name == "graph_profile")
    assert "guess" in str(profile.body) and "grade=refuted" in str(profile.body)
    assert await wire.SqlReviewTargets(scenario.engine).due_review_targets(UNIT, now) == ()


async def test_lesson_refuses_second_grade_for_same_tally(scenario):
    tally = await lesson(scenario)
    assert await submit(scenario, proposal("holds", "lesson", (tally["graph_id"],))) == Stored(
        "grade",
    )
    repeat = proposal("weakened", "lesson", (tally["graph_id"],), id_="second")
    assert await submit(scenario, repeat) == Refusal("already_graded", "links")
    assert await grade_rows(scenario) == [("grade", "holds", REVIEW.model)]


async def test_lesson_accepts_second_grade_after_tally_advances(scenario):
    tally = await lesson(scenario)
    first = proposal("holds", "lesson", (tally["graph_id"],))
    assert await submit(scenario, first) == Stored("grade")
    updated = await changed_tally(scenario, "next-leak")
    assert updated["asserted_at"] > tally["asserted_at"]
    assert updated["graph_id"] == tally["graph_id"] and updated["contradiction"] == 2
    repeat = proposal("weakened", "lesson", (updated["graph_id"],), id_="second")
    stale = replace(repeat, reviewed_at=tally["asserted_at"])
    assert await submit(scenario, stale) == Refusal("stale_base", "target")
    assert await submit(scenario, repeat) == Stored("second")
    assert await grade_rows(scenario) == [
        ("grade", "holds", REVIEW.model), ("second", "weakened", REVIEW.model),
    ]
    async with scenario.engine.connect() as conn:
        assert set(await conn.scalars(sa.select(EDGE.c.src_id).where(
            EDGE.c.type == "grades", EDGE.c.dst_id == "lesson", EDGE.c.state == "live",
        ))) == {"grade", "second"}
        assert await conn.scalar(sa.select(LESSON_TALLY.c.contradiction)) == 2
