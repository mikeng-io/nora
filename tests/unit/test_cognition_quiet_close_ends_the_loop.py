"""A session whose every item closed `no_change` or `covered`, with no other verb
called, ends at the last close: no further model call (DEC-279).

Without the early end, Strands makes one more model call that writes nothing after the last close. A
session that wrote, closed an item `waiting`, or called any other verb keeps that call.

Driven through the REAL session runner, the REAL `LiveCognitionAgentRunner` and
a real Strands loop over a scripted model, so `model.turn` counts the model
calls the loop actually made. Names are invented and no message is quoted.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterable, Sequence
from typing import Any

import pytest
from strands.models import Model

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


# ---- one model turn holding `close` and another verb ----


class _BatchModel(Model):
    """One scripted assistant turn per entry: a list of `(tool, input)` calls
    made TOGETHER in that turn, or None to stop. Strands runs every tool of a
    turn, then reads `stop_event_loop` once."""

    def __init__(self, turns: Sequence[Sequence[tuple[str, str]] | None]) -> None:
        self._turns = list(turns)
        self.turn = 0

    def update_config(self, **model_config: Any) -> None:
        return None

    def get_config(self) -> Any:
        return {}

    async def structured_output(self, output_model: Any, prompt: Any = None,
                                **kwargs: Any) -> Any:
        raise NotImplementedError

    async def stream(self, *args: Any, **kwargs: Any) -> AsyncIterable[Any]:
        calls = self._turns[self.turn] if self.turn < len(self._turns) else None
        self.turn += 1
        yield {"messageStart": {"role": "assistant"}}
        if not calls:
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"text": "done"}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}
        else:
            for n, (name, call_input) in enumerate(calls):
                yield {"contentBlockStart": {"start": {"toolUse": {
                    "name": name, "toolUseId": f"t{self.turn}-{n}"}}}}
                yield {"contentBlockDelta": {"delta": {"toolUse": {"input": call_input}}}}
                yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        yield {"metadata": {
            "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
            "metrics": {"latencyMs": 1},
        }}


async def _run_batch(turns: list[Any]) -> tuple[_BatchModel, Any]:
    model = _BatchModel(turns)
    runner = LiveCognitionAgentRunner(
        models=_Models(model),  # type: ignore[arg-type]
        metrics=Metrics(),
        retry=factory.RetryPolicy(max_attempts=1, initial_delay=0, max_delay=0),
    )

    async def unused(by: dict[str, Any], tools: list[Any]) -> None:
        raise AssertionError("the live runner drives this session")

    _, outcome = await session(unused, runner_kwargs={"agent_runner": runner})
    return model, outcome


_ACCEPTED = json.dumps({**NOTE, "body": GOOD_BODY})
_REFUSED = json.dumps({**NOTE, "body": GOOD_BODY.replace("[101]", "[999]")})


@pytest.mark.parametrize("write", [_ACCEPTED, _REFUSED], ids=["accepted", "refused"])
@pytest.mark.parametrize("close_first", [True, False], ids=["close_first", "write_first"])
async def test_a_write_in_the_same_turn_as_a_quiet_close_keeps_the_follow_up_call(
    write, close_first,
):
    """Strands reads `stop_event_loop` only after EVERY tool of the turn ran, so
    a quiet `close` and a write in one response must not end the loop: the
    model has to see what the write answered. Either order, accepted or
    refused: two model calls."""
    calls = [("close", _close(0, "no_change")), ("note_create", write)]
    model, outcome = await _run_batch([calls if close_first else calls[::-1], None])
    assert [c.reason for c in outcome.closed] == ["no_change"]
    assert model.turn == 2


async def test_a_quiet_close_alone_in_its_turn_still_makes_one_model_call():
    model, _ = await _run_batch([[("close", _close(0, "no_change"))], None])
    assert model.turn == 1
