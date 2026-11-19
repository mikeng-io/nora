"""The fire path journals the debrief while the room slot still owns the outcome."""

import asyncio

import pytest

from nora.access import IdentityAccess
from nora.app.fire import SENT, SILENT
from nora.app.fire_engagement import AmbientEngagement
from nora.engagement.graph_context import EngagementGraph
from nora.engagement.slots import ConversationSlots
from nora.engagement.stages import graph_debrief
from nora.engagement.stages.debrief import Completion
from nora.memory import ActAuthority, AssembledPass, PassContext, PassItem, act_pass
from nora.policy import Conversation, Policy
from nora.testing import FakeAgentRunner, ScriptedTurn
from nora.testing.fakes import FakeRecordStore
from tests.unit.test_fire_path import T0, _fire_path, _participation, _send_turns
from tests.unit.test_fire_path import metrics as metrics
from tests.unit.test_fire_path import persons as persons

ROOM = ("signal", "conv-1")
UNIT = "fixture-unit"


class Journal:
    def __init__(self, slots):
        self.slots = slots
        self.events = []
        self.pending = {}
        self.outcomes = {}

    async def take_oldest(self, unit, room):
        assert self.slots.held(ROOM)
        self.events.append("retry")
        return next((r for r in self.pending.values() if (r.unit, r.room) == (unit, room)), None)

    async def begin(self, row):
        assert self.slots.held(ROOM)
        assert row.pass_id in self.outcomes
        self.events.append("debrief")
        if row.pass_id in self.pending:
            return False
        self.pending[row.pass_id] = row
        return True

    async def items(self, row):
        kind = self.outcomes[row.pass_id]
        return (PassItem("result:" + row.pass_id, kind, T0, "nora", None),)

    async def room_messages(self, row):
        return ()

    async def settle(self, row, now):
        self.events.append("settled")
        del self.pending[row.pass_id]

    async def record_silence(self, authority):
        self.events.append("silence")
        self.outcomes[authority.pass_context.pass_id] = "silence"

    def pass_scope(self, ctx, occasion, model, doctrine):
        self.authority = ActAuthority(UNIT, pass_context=PassContext(
            UNIT, ROOM[1], False, occasion, "engagement", ctx.tag_id, "",
            model, "", doctrine, "", "", "", "",
        ))
        return act_pass(self.authority, self)

    async def run(self, executor, target, event, ordinal, invoke, **kwargs):
        result = await invoke()
        if result.ok:
            self.events.append("send")
            self.outcomes[self.authority.pass_context.pass_id] = "send_outcome"
        return result

    async def record_tool(self, *args):
        pass


class ClosingModel:
    model = "fixture-closing-model"

    def __init__(self, failure):
        self.failure = failure
        self.kinds = []

    async def complete(self, *, system, prompt):
        assert system == "Re-judge this pass."
        kind = "send_outcome" if '"kind": "send_outcome"' in prompt else "silence"
        assert f'"kind": "{kind}"' in prompt
        self.kinds.append(kind)
        if self.failure and len(self.kinds) == 1:
            raise TimeoutError
        return Completion('{"operations": []}', "stop")

    def objective_body(self, name):
        assert name == "debrief"
        return "Re-judge this pass."

    async def assemble(self, name, data, floor, scope, now):
        assert name == "debrief"
        assert len(data.items) == 1 and scope.unit == UNIT
        return AssembledPass(tuple(floor), "ok", None)


def _setup(persons, doctrine_store, metrics, *, silent, fail=False):
    convo = Conversation(ROOM[0], frozenset({ROOM[1]}), frozenset(), unit=UNIT)
    policy = Policy((convo,), _by_id={ROOM: convo})
    slots = ConversationSlots(maxlen=1, spawn=asyncio.create_task)
    store = Journal(slots)
    records = FakeRecordStore()
    records.delivery_acts = store
    model = ClosingModel(fail)
    wiring = graph_debrief.Wiring(
        store, None, model, model, model, lambda: T0, dict, metrics, 1.0, 1.0,
    )
    ambient = AmbientEngagement(EngagementGraph(IdentityAccess.for_files(persons, policy)), wiring)
    turns = [ScriptedTurn(final_text="")] if silent else _send_turns("A generated reply")
    runner = FakeAgentRunner(turns)
    path = _fire_path(
        persons=persons, doctrine_store=doctrine_store, metrics=metrics,
        runner=runner, participation_runner=runner,
        records=records, policy=policy, ambient=ambient,
    )
    return slots, store, path, runner, model


async def _speak(slots, path):
    assert slots.try_acquire(ROOM)
    try:
        return await path.speak(_participation(ROOM[1]))
    finally:
        slots.release_and_dispatch(ROOM)


@pytest.mark.parametrize("silent", [False, True], ids=["send", "silence"])
async def test_the_ambient_debrief_runs_once_inside_the_slot_after_the_outcome(
    persons, doctrine_store, metrics, silent,
):
    slots, store, path, runner, model = _setup(persons, doctrine_store, metrics, silent=silent)
    assert await _speak(slots, path) == (SILENT if silent else SENT)
    assert store.events == ["retry", "silence" if silent else "send", "debrief", "settled"]
    assert model.kinds == ["silence" if silent else "send_outcome"]
    assert runner.invocations == 1
    assert not slots.held(ROOM)


async def test_an_ambient_debrief_failure_retries_the_obligation_before_another_turn(
    persons, doctrine_store, metrics,
):
    slots, store, path, runner, model = _setup(
        persons, doctrine_store, metrics, silent=True, fail=True,
    )
    assert await _speak(slots, path) == SILENT
    assert len(store.pending) == runner.invocations == 1
    assert model.kinds == ["silence"]

    assert await _speak(slots, path) == SILENT
    assert store.events == [
        "retry", "silence", "debrief", "retry", "settled", "silence", "debrief", "settled",
    ]
    assert not store.pending
    assert runner.invocations == 2
