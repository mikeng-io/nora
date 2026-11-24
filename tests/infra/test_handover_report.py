"""A handed-over call is reported once, whatever its outcome, through the real condition matcher.

The report is an intention whose trigger is an act event on the hand-over's act. These tests run
the production matcher and the production proactive claims over PostgreSQL, and count what the
proactive pass would be given: one due occurrence, claimed once.
"""

# ruff: noqa: F811 - pytest resolves imported fixture names.

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from nora.app.handover_sweep import sweep_handovers
from nora.infra.sql.cognitive_graph_schema import INTENTION, NORA_ACT, NORA_ACT_OUTCOME
from nora.infra.sql.graph_stimuli import SqlGraphStimuli
from nora.memory import wire
from nora.memory.adapters.act_store import SqlActStore
from nora.memory.adapters.proactive_claims import SqlProactiveClaims
from nora.memory.domain.acts import Act, ActAuthority, ActIdentity, Outcome
from tests.infra.test_condition_matcher import _health, _intention, _platform_act
from tests.infra.test_item_registration import (
    _PERSON,
    _ROOM,
    _UNIT,
    _registration,
    engine,  # noqa: F401
)

LATER = datetime.now(UTC) + timedelta(days=1)


def _handover(*, intention: str | None = None) -> Act:
    return Act(
        ActIdentity("tool.handover", "pass-1", 0), "delegation", "tool-call:pass-1:t0",
        ActAuthority(_UNIT, _PERSON, intention, _ROOM, None), "unknown",
    )


def _report(act: Act) -> dict:
    return {"kind": "condition", "form": "act_event", "value": {"act_id": act.identity.id}}


async def _commit_report(engine, act: Act, id_: str = "report", **fields) -> None:
    """The report intention a pass or its debrief commits: a trigger on the act, delivered once."""
    await _intention(engine, id_, "act_event", {"own_delivery": True}, trigger=_report(act),
                     **fields)


async def _due(engine) -> list:
    return [s for s in await SqlGraphStimuli(engine).read(_UNIT) if s.kind == "intention_due"]


async def _states(engine) -> dict[str, set[str]]:
    """The row states this behaviour depends on: a live act and outcome, an open intention."""
    async with engine.connect() as conn:
        return {
            "act": set(await conn.scalars(sa.select(NORA_ACT.c.state))),
            "outcome": set(await conn.scalars(sa.select(NORA_ACT_OUTCOME.c.state))),
            "intention": set(await conn.scalars(sa.select(INTENTION.c.state))),
        }


async def _tick(engine) -> None:
    await wire.condition_matcher(engine).run_once()


@pytest.mark.parametrize("verdict", ["done", "failed", "uncertain"])
async def test_each_outcome_of_a_handed_over_call_gives_exactly_one_report(engine, verdict):
    store, act = SqlActStore(engine, _registration()), _handover()
    await _commit_report(engine, act)
    assert await store.reserve(act)
    await _tick(engine)
    assert await _due(engine) == []  # reserved, no outcome yet: nothing fires at launch

    await store.observe(act, Outcome(verdict))
    await _tick(engine)
    await _tick(engine)

    assert await _states(engine) == {
        "act": {"live"}, "outcome": {"live"}, "intention": {"open"},
    }
    [due] = await _due(engine)
    assert (due.graph_id, due.occurrence) == ("report", await _act_graph_id(engine))
    claims = SqlProactiveClaims(engine)
    [available] = await claims.due_intentions(LATER)
    assert await claims.claim(available, LATER) is not None
    assert await claims.due_intentions(LATER) == ()


async def _act_graph_id(engine) -> str:
    async with engine.connect() as conn:
        return await conn.scalar(sa.select(NORA_ACT.c.graph_id))


async def test_an_uncertain_outcome_later_settled_to_done_gives_one_report_not_two(engine):
    store, act = SqlActStore(engine, _registration()), _handover()
    await _commit_report(engine, act)
    assert await store.reserve(act)
    await store.observe(act, Outcome("uncertain"))
    await _tick(engine)
    [first] = await _due(engine)

    await store.observe(act, Outcome("done"))
    await _tick(engine)

    assert await _due(engine) == [first]


async def test_a_swept_handover_gives_exactly_one_report(engine):
    engine.update_execution_options(item_registration=_registration())
    store, act = SqlActStore(engine, _registration()), _handover()
    await _commit_report(engine, act)
    assert await store.reserve(act)
    await _tick(engine)
    assert await _due(engine) == []

    assert await sweep_handovers(engine) == 1
    await _tick(engine)
    await sweep_handovers(engine)
    await _tick(engine)

    assert len(await _due(engine)) == 1


async def test_a_report_the_debrief_commits_after_the_outcome_is_still_one_report(engine):
    store, act = SqlActStore(engine, _registration()), _handover()
    assert await store.reserve(act)
    await store.observe(act, Outcome("done"))
    await _tick(engine)
    assert await _due(engine) == []  # nobody promised a report: nothing is delivered

    await _commit_report(engine, act, asserted_at=datetime.now(UTC))
    await _tick(engine)
    await _tick(engine)

    assert len(await _due(engine)) == 1


async def test_a_proactive_pass_that_hands_over_moves_its_own_intention(engine):
    store, act = SqlActStore(engine, _registration()), _handover(intention="report")
    await _commit_report(engine, act)
    await _intention(engine, "other", "act_event", {"own_delivery": True}, trigger=_report(act))
    assert await store.reserve(act)
    await store.observe(act, Outcome("done"))
    await _tick(engine)
    assert [(s.graph_id) for s in await _due(engine)] == ["report"]


async def test_no_committed_intention_means_no_report(engine):
    store, act = SqlActStore(engine, _registration()), _handover()
    assert await store.reserve(act)
    await store.observe(act, Outcome("done"))
    await _tick(engine)
    assert await _due(engine) == []
    assert (await _states(engine))["outcome"] == {"live"}  # positive control: the act ran


@pytest.mark.parametrize("verdict", ["done", "failed", "uncertain"])
async def test_an_act_event_on_a_platform_delivery_still_waits_for_a_healthy_source(
    engine, verdict,
):
    store, act = SqlActStore(engine, _registration()), _platform_act()
    act = replace(act, authority=replace(act.authority, unit=_UNIT))
    await _commit_report(engine, act)
    assert await store.reserve(act)
    await store.observe(act, Outcome(verdict))
    await _health(engine, "signal", "down")
    await _tick(engine)
    assert await _due(engine) == []

    await _health(engine, "signal", "up", at=datetime.now(UTC) - timedelta(seconds=1))
    await _tick(engine)
    assert len(await _due(engine)) == 1
