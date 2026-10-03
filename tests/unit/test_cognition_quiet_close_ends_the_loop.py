"""A session whose every item closed `no_change` or `covered`, with no other verb
called, ends at the last close: no further model call (DEC-279).

Measured live 2026-10-03: after the model closed the last item, Strands made one
more model call that wrote nothing, on every session that closed quiet. A
session that wrote, closed an item `waiting`, or called any other verb keeps
that call, as before.

Driven through the REAL session runner, the REAL `LiveCognitionAgentRunner` and
a real Strands loop over a scripted model, so `model.turn` counts the model
calls the loop actually made. Names are invented and no message is quoted.
"""

from __future__ import annotations

import json
from typing import Any

from nora.agent import factory
from nora.app.cognition_agent_runner import LiveCognitionAgentRunner
from nora.obs.metrics import Metrics
from tests.unit.cognition_rig import GOOD_BODY, NOTE, A, B, session
from tests.unit.test_dream_memory_budget import _ScriptedModel


class _Models:
    def __init__(self, model: _ScriptedModel) -> None:
        self._model = model

    def create(self) -> _ScriptedModel:
        return self._model


def _close(index: int, reason: str) -> str:
    return json.dumps({"item_index": index, "reason": reason})


async def _run(
    script: list[str | None], inputs: list[str], *, rooms: tuple[Any, ...] = (A,),
) -> tuple[_ScriptedModel, Any]:
    model = _ScriptedModel(script, inputs=inputs)
    runner = LiveCognitionAgentRunner(
        models=_Models(model), metrics=Metrics(),
        retry=factory.RetryPolicy(max_attempts=1, initial_delay=0, max_delay=0),
    )

    async def unused(by: dict[str, Any], tools: list[Any]) -> None:
        raise AssertionError("the live runner drives this session")

    _, outcome = await session(unused, rooms=rooms, runner_kwargs={"agent_runner": runner})
    return model, outcome


async def test_a_session_that_closes_every_item_no_change_makes_exactly_one_model_call():
    model, outcome = await _run(["close", None], [_close(0, "no_change"), ""])
    assert model.turn == 1
    assert [c.reason for c in outcome.closed] == ["no_change"]


async def test_the_loop_ends_only_once_the_last_item_is_closed():
    """Two items: the close of the first leaves the loop running; the second,
    `covered`, ends it."""
    model, outcome = await _run(
        ["close", "close", None], [_close(0, "no_change"), _close(1, "covered"), ""],
        rooms=(A, B),
    )
    assert model.turn == 2
    assert sorted(c.reason for c in outcome.closed) == ["covered", "no_change"]


async def test_a_session_with_a_written_close_keeps_the_follow_up_call():
    note = json.dumps({**NOTE, "body": GOOD_BODY})
    model, outcome = await _run(
        ["note_create", "close", None], [note, _close(0, "written"), ""],
    )
    assert [c.reason for c in outcome.closed] == ["written"]
    assert model.turn == 3


async def test_a_waiting_close_keeps_the_follow_up_call():
    model, outcome = await _run(["close", None], [_close(0, "waiting"), ""])
    assert [c.reason for c in outcome.closed] == ["waiting"]
    assert model.turn == 2


async def test_any_other_verb_called_keeps_the_follow_up_call_even_when_it_was_refused():
    """A write the validator refused is still a verb the model called: it may
    want another turn to correct it, so a quiet close does not end the loop."""
    refused = json.dumps({**NOTE, "body": GOOD_BODY.replace("[101]", "[999]")})
    model, outcome = await _run(
        ["note_create", "close", None], [refused, _close(0, "no_change"), ""],
    )
    assert [c.reason for c in outcome.closed] == ["no_change"]
    assert model.turn == 3
