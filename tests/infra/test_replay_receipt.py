"""Coverage and durable receipts over the actual historical wake inputs."""

from dataclasses import replace
from datetime import timedelta

import sqlalchemy as sa

from nora.access import IdentityAccess
from nora.agent.lineage import LineagePolicy
from nora.app.composition_wake_history import WakeHistoryRooms
from nora.app.graph_replay import GraphReplay
from nora.domain.lineage import room_key
from nora.infra.sql.cognitive_graph_schema import GRAPH_REPLAY_RECEIPT, REGISTRY, WATCH_RESULT
from nora.infra.sql.cognitive_graph_store import content_digest
from nora.infra.sql.schema import MESSAGES
from nora.memory import wire
from nora.testing.scripted_agent import ScriptedTurn
from tests.infra.test_retrieval_scope import AT, OTHER_ROOM, ROOM, UNIT, write_edge
from tests.infra.test_retrieval_scope import scenario as scenario
from tests.infra.test_wake_runner import WakeModel, runner

LATER = AT + timedelta(minutes=1)


def receipts(scenario):
    access = IdentityAccess(file_persons=scenario.persons, file_policy=scenario.policy)
    return wire.replay_receipts(
        scenario.engine, access, LineagePolicy.from_policy(scenario.policy, access=access),
        scenario.policy,
    )


class ScriptedReplay:
    def __init__(self, scenario, scripts):
        self._scenario = scenario
        self._scripts = iter(scripts)
        self.calls = []
        self.models = []

    async def run(self, claim):
        turns, limit = next(self._scripts)
        model = WakeModel(turns)
        self.calls.append(claim)
        self.models.append(model)
        scenario = self._scenario
        access = IdentityAccess(file_persons=scenario.persons, file_policy=scenario.policy)
        wake = runner(
            scenario, model, turns=limit, occasion="wake_history", offered=wire.WAKE_HISTORY_TOOLS,
            rooms=WakeHistoryRooms(scenario.policy, access, "Nora"),
        )
        return await wake.run(claim)


async def receipt_rows(scenario):
    table = GRAPH_REPLAY_RECEIPT
    async with scenario.engine.connect() as conn:
        return (await conn.execute(sa.select(table).order_by(
            table.c.clock, table.c.recorded_at,
        ))).mappings().all()


async def test_a_closed_replay_records_the_exact_inputs_and_a_failed_replay_records_none(scenario):
    await scenario.item("first")
    await scenario.item("second", at=LATER)
    last = LATER + timedelta(seconds=1)
    await scenario.item("third", at=last)
    store = receipts(scenario)
    replay = GraphReplay(store, 1)
    refused_to_finish = ScriptedTurn(tool_calls=[("memory_read", {"id": "second"})])
    model = ScriptedReplay(scenario, [([], 4), ([refused_to_finish], 1), ([], 4)])

    interrupted = await replay.run(UNIT, last, model)
    assert interrupted.closed == 1 and interrupted.failed == "bound"
    assert len(model.calls) == 2
    assert [item.graph_id for item, _ in interrupted.coverage.pending] == ["second", "third"]
    [saved] = await receipt_rows(scenario)
    assert (saved["unit"], saved["pass_id"], saved["clock"]) == (UNIT, model.calls[0].pass_id, AT)
    async with scenario.engine.connect() as conn:
        row = (await conn.execute(sa.select(MESSAGES).where(
            MESSAGES.c.graph_id == "first",
        ))).mappings().one()
    assert saved["inputs"] == {"first": content_digest("messages", row)}

    resumed = ScriptedReplay(scenario, [([], 4), ([], 4)])
    complete = await replay.run(UNIT, last, resumed)
    assert complete.closed == 2 and complete.failed is None
    assert complete.coverage.pending == ()
    assert [tuple(saved["inputs"]) for saved in await receipt_rows(scenario)] == [
        ("first",), ("second",), ("third",),
    ]


async def test_operator_watch_result_is_counted_without_queueing_or_receipting_it(scenario):
    await scenario.item("first")
    await scenario.claim("claim")
    await write_edge(scenario, "claim", "first", "cites")
    async with scenario.engine.begin() as conn:
        await conn.execute(sa.insert(REGISTRY).values(
            id="private-watch", unit=UNIT, kind="item",
        ))
        await conn.execute(sa.insert(WATCH_RESULT).values(
            graph_id="private-watch", unit=UNIT, occurred_at=AT, origin_room=ROOM,
            watch_edge="edge:cites:claim:first", content_hash="generated-watch-hash",
            exposure="operator",
        ))
    model = ScriptedReplay(scenario, [([], 4)])
    report = await GraphReplay(receipts(scenario), 4).run(UNIT, AT, model)
    assert report.coverage.not_observable == {"private-watch": "operator_exposure"}
    assert report.coverage.pending == ()
    [saved] = await receipt_rows(scenario)
    assert set(saved["inputs"]) == {"first"}
    [call] = model.calls
    assert [stimulus.graph_id for stimulus in call.stimuli] == ["first"]


async def test_items_from_two_rooms_are_batched_and_read_in_their_respective_rooms(scenario):
    await scenario.item("first")
    await scenario.item("second", room=OTHER_ROOM, at=LATER)
    model = ScriptedReplay(scenario, [([], 4), ([], 4)])
    report = await GraphReplay(receipts(scenario), 4).run(UNIT, LATER, model)
    assert report.closed == 2 and report.failed is None
    assert [batch.reading_room for batch in model.calls] == [
        room_key("signal", ROOM), room_key("signal", OTHER_ROOM),
    ]
    agendas = [call.prompt().split("</agenda>", 1)[0].split("<agenda", 1)[1]
               for call in model.models]
    assert "first | basis" in agendas[0] and "second | basis" not in agendas[0]
    assert "second | basis" in agendas[1] and "first | basis" not in agendas[1]


async def test_roomless_items_use_the_home_room_and_a_unit_without_one_counts_them(scenario):
    await scenario.item("roomless")
    async with scenario.engine.begin() as conn:
        await conn.execute(sa.update(MESSAGES).where(MESSAGES.c.graph_id == "roomless").values(
            origin_room=None,
        ))
    report = await GraphReplay(receipts(scenario), 4).inspect(UNIT, AT)
    assert report.coverage.not_observable == {"roomless": "no_home_room"}
    assert report.batches == ()
    policy_rooms = tuple(
        replace(room, home_room=True) if room.primary_id == ROOM else room
        for room in scenario.policy.rooms()
    )
    scenario.policy = replace(scenario.policy, conversations=policy_rooms)
    model = ScriptedReplay(scenario, [([], 4)])
    report = await GraphReplay(receipts(scenario), 4).run(UNIT, AT, model)
    assert report.closed == 1 and report.coverage.not_observable == {}
    assert model.calls[0].reading_room == room_key("signal", ROOM)
