"""Verifier verdicts maintain the exact head's tally and wake trigger."""

from datetime import timedelta

import pytest
import sqlalchemy as sa

from nora.infra.sql.cognitive_graph_schema import EDGE, LESSON_TALLY
from nora.infra.sql.graph_stimuli import SqlGraphStimuli
from nora.memory.adapters.lesson_tally import rebuild_lesson_tally
from nora.memory.adapters.wake_claims import SqlWakeClaims
from nora.memory.app.admission import Stored
from nora.memory.domain.admission import Refusal
from nora.memory.domain.admission_proposals import Retire, Revise, Scope
from nora.memory.domain.pass_inputs import WakeInput
from tests.infra.test_graph_propose import WAKE
from tests.infra.test_intention_verdict import _tick
from tests.infra.test_lesson_admission import admit, correction, proposal
from tests.infra.test_lesson_admission import lesson_setup as lesson_setup
from tests.infra.test_pass_anchors import NOW, SCOPE
from tests.infra.test_pass_assembly import _assembler
from tests.infra.test_retrieval_scope import AT, UNIT
from tests.infra.test_retrieval_scope import scenario as scenario
from tests.infra.test_situation_staleness import intention, serves
from tests.infra.test_wake_claims import GROUP, ORIGIN, POLICY


async def resting(scenario, source, target):
    await scenario.graph.create(EDGE, {
        "id": f"edge:rests_on:{source}:{target}", "unit": UNIT, "kind": "edge",
        "basis": "committed", "state": "live", "exposure": "internal", "writer": "fixture",
        "type": "rests_on", "src_id": source, "dst_id": target, "asserted_at": AT,
    })


async def ended(scenario, id_, ending, *, lesson=None, parent=None, **fields):
    await intention(scenario, id_, state="ended", ending=ending, ended_at=AT, **fields)
    if lesson is not None:
        await resting(scenario, id_, lesson)
    if parent is not None:
        await serves(scenario, id_, parent)


@pytest.fixture
async def held_lesson(scenario, lesson_setup):
    svc, _checker = lesson_setup
    await correction(scenario)
    answer = await admit(svc, ("correction",))
    assert isinstance(answer, Stored)
    return svc, answer.id


async def tally_rows(scenario):
    async with scenario.engine.connect() as conn:
        return {row["claim_id"]: dict(row) for row in (
            await conn.execute(sa.select(LESSON_TALLY))
        ).mappings()}


async def tallies(scenario):
    rows = await tally_rows(scenario)
    return {id_: (row["support"], row["contradiction"]) for id_, row in rows.items()}


async def dependencies(scenario):
    return tuple((s.graph_id, s.occurrence) for s in await SqlGraphStimuli(scenario.engine).read(
        UNIT,
    ) if s.kind == "dependency" and s.source == "lesson_tally")


async def rebuild(scenario, lesson):
    async with scenario.engine.begin() as conn:
        await rebuild_lesson_tally(conn, scenario.graph, unit=UNIT, claim_id=lesson, at=NOW)


async def test_lesson_tallies_resting_verdicts_and_renders_counts(scenario, held_lesson):
    _, lesson = held_lesson
    assert await tallies(scenario) == {lesson: (0, 0)}
    for ending in ("achieved", "leaked", "dropped", "unknown"):
        await ended(scenario, ending, ending, lesson=lesson)
    await intention(scenario, "parent", form="maintain", done_when=None, done_when_form=None,
                    holds_while="Generated practice", holds_while_form="predicate")
    await resting(scenario, "parent", lesson)
    # The same verdict reaching a lesson by both paths counts only once.
    await ended(scenario, "child", "achieved", lesson=lesson, parent="parent")
    await ended(scenario, "other-child", "leaked", parent="parent")
    await ended(scenario, "unrelated", "achieved")
    await _tick(scenario.engine, NOW)

    assert await tallies(scenario) == {lesson: (2, 2)}
    before = await tally_rows(scenario)
    async with scenario.engine.begin() as conn:
        await conn.execute(sa.delete(LESSON_TALLY))
    await rebuild(scenario, lesson)
    assert await tally_rows(scenario) == before

    now = await scenario.now()
    view = await _assembler(scenario).assemble("wake", WakeInput(()), (), SCOPE, now)
    assert "support=2, contradiction=2" in view.render()


async def test_tally_publishes_the_overtaken_lesson_once_per_counts_and_wake_claims_it(
    scenario, held_lesson,
):
    _, lesson = held_lesson
    await ended(scenario, "first-leak", "leaked", lesson=lesson)
    now = await scenario.now()
    await _tick(scenario.engine, now)
    assert await dependencies(scenario) == ((lesson, "0:1"),)
    await rebuild(scenario, lesson)
    assert await dependencies(scenario) == ((lesson, "0:1"),)

    claim = await SqlWakeClaims(scenario.engine).claim(
        GROUP, ORIGIN, now + timedelta(seconds=5), POLICY,
    )
    assert claim is not None
    assert any(s.graph_id == lesson and s.occurrence == "0:1" for s in claim.stimuli)
    assembled = await _assembler(scenario).assemble(
        "wake", WakeInput(claim.stimuli), (), SCOPE, now + timedelta(seconds=5),
    )
    assert lesson in assembled.shown
    await ended(scenario, "second-leak", "leaked", lesson=lesson)
    await _tick(scenario.engine, NOW)
    assert set(await dependencies(scenario)) == {(lesson, "0:1"), (lesson, "0:2")}

    for index in range(3):
        await ended(scenario, f"success:{index}", "achieved", lesson=lesson)
        await _tick(scenario.engine, NOW)
    assert await tallies(scenario) == {lesson: (3, 2)}
    assert set(await dependencies(scenario)) == {(lesson, "0:1"), (lesson, "0:2"), (lesson, "1:2")}


async def test_only_the_current_lesson_head_can_publish_a_tally(scenario, held_lesson):
    svc, old = held_lesson
    await ended(scenario, "first-leak", "leaked", lesson=old)
    await _tick(scenario.engine, NOW)

    candidate = proposal(("correction",))
    assert not isinstance(candidate, Refusal)
    change = Revise(base=old, replacement=candidate)
    [revised] = (await svc.admit((change,), WAKE, (old, "correction", "nora-pointer"))).outcomes
    assert isinstance(revised, Stored)
    assert await tallies(scenario) == {old: (0, 1), revised.id: (0, 0)}

    await ended(scenario, "old-leak", "leaked", lesson=old)
    await _tick(scenario.engine, NOW)
    assert await dependencies(scenario) == ((old, "0:1"),)
    await ended(scenario, "new-leak", "leaked", lesson=revised.id)
    await _tick(scenario.engine, NOW)
    assert set(await dependencies(scenario)) == {(old, "0:1"), (revised.id, "0:1")}

    retire = Retire(
        id="retired-lesson", base=revised.id, scope=Scope(UNIT, exposure=None), basis="inferred",
        reasoning="deduced", confidence="high", citations=("correction",),
    )
    assert (await svc.admit((retire,), WAKE, (revised.id, "correction"))).outcomes == (
        Stored("retired-lesson", revised.id),
    )
    await ended(scenario, "retired-leak", "leaked", lesson=revised.id)
    await _tick(scenario.engine, NOW)
    assert (await tallies(scenario))[revised.id] == (0, 2)
    assert set(await dependencies(scenario)) == {(old, "0:1"), (revised.id, "0:1")}


async def test_tally_inherits_restrictions_from_the_resting_verdict(scenario, held_lesson):
    _, lesson = held_lesson
    await ended(scenario, "restricted", "leaked", lesson=lesson, exposure="operator")
    now = await scenario.now()
    await _tick(scenario.engine, now)

    assert await tallies(scenario) == {lesson: (0, 1)}
    assert (await tally_rows(scenario))[lesson]["exposure"] == "operator"
    view = await _assembler(scenario).assemble("wake", WakeInput(()), (), SCOPE, now)
    assert lesson in view.shown
    assert "support=0, contradiction=1" not in view.render()
