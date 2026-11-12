"""The judge's bounded digest: a wake whose trajectory repeats the whole
session context still feeds the judge a digest under the ceiling that
lists every tool call and every diff entry, and an over-budget digest is
ERROR naming its size — never a silent truncation.
"""

from __future__ import annotations

import json
from typing import Any

from scripts.cognition_loop.cases import Expectation
from scripts.cognition_loop.judges import (
    JUDGE_INPUT_MAX_BYTES,
    judge_one,
    judge_payload,
)
from scripts.cognition_loop.state import (
    Entry,
    KindDiff,
    StateDiff,
    UnitState,
)


def _expectation() -> Expectation:
    return Expectation(
        wake=0, polarity="must", kind="note",
        text="the generated note exists after the wake")


def _diff() -> StateDiff:
    return StateDiff(kinds={
        "note": KindDiff(
            "note",
            created=(Entry("created", "n-1",
                           after={"title": "generated"}),),
            revised=(Entry("revised", "n-2",
                           before={"body": "a"}, after={"body": "b"}),),
            retired=(Entry("retired", "n-3", before={"body": "old"}),),
            unchanged=tuple(Entry("unchanged", f"n-{i}",
                                  before={"x": 1}, after={"x": 1})
                            for i in range(4, 40)),
        ),
        "memo": KindDiff("memo"),
    })


def _state() -> UnitState:
    return UnitState(kinds={"note": {"n-1": {"title": "generated"}}})


def _trajectory() -> dict[str, Any]:
    # Each request repeats the whole session context: three exchanges at
    # ~1.5 MB each is a ~4.5 MB trajectory.
    context = "the whole session context " * 60_000
    return {
        "model_calls": [
            {"messages": [
                {"role": "user", "content": [{"text": context}]},
                {"role": "assistant", "content": [{"text": "reasoning"}]},
            ]}
            for _ in range(3)
        ] + [{"messages": [
            {"role": "user", "content": [{"text": context}]},
            {"role": "assistant",
             "content": [{"text": "the final reasoning"}]},
        ]}],
        "tool_calls": [
            {"tool": t, "arguments": {"i": i},
             "result": {"status": "success",
                        "content": [{"json": {"status": "ok", "id": f"e-{i}"}}]},
             "refusal": None, "error_type": None}
            for i, t in enumerate(
                ["note_create", "note_revise", "memo_create", "wake_at"])
        ],
        "stop_reason": "end_turn",
        "usage": {"tokens": 750_671},
    }


def test_a_multi_megabyte_trajectory_still_yields_a_bounded_digest():
    payload = judge_payload(_expectation(), _diff(), _state(), _trajectory())
    size = len(json.dumps(payload, ensure_ascii=False, default=str).encode())
    assert size < JUDGE_INPUT_MAX_BYTES


def test_the_digest_lists_every_tool_call_and_every_diff_entry():
    payload = judge_payload(_expectation(), _diff(), _state(), _trajectory())
    assert [c["tool"] for c in payload["tool_calls"]] == [
        "note_create", "note_revise", "memo_create", "wake_at"]
    assert [e["id"] for e in payload["diff"]["note"]["created"]] == ["n-1"]
    assert [e["id"] for e in payload["diff"]["note"]["revised"]] == ["n-2"]
    assert [e["id"] for e in payload["diff"]["note"]["retired"]] == ["n-3"]
    # Unchanged is a count only — never the entries themselves.
    assert payload["diff"]["note"]["unchanged"] == 36
    assert payload["final_output"] == "the final reasoning"
    assert payload["stop_reason"] == "end_turn"
    assert payload["usage"] == {"tokens": 750_671}


class _RecordingJudges:
    """`input_budget` is the judge's context ceiling; `call` records so a
    test can assert the model was never reached."""

    def __init__(self, *, budget: int | None = None):
        self.calls: list[Any] = []
        self.served: dict[str, Any] = {}
        if budget is not None:
            self.input_budget = budget

    def _endpoint(self, model: str):
        return ("http://x", "key", "routed")

    async def call(self, model, instruction, payload):
        self.calls.append(payload)
        return '{"verdict": "met", "reason": "x", "entry_ids": []}'


async def test_an_over_budget_digest_is_error_and_never_reaches_the_judge():
    judges = _RecordingJudges(budget=64)
    answer = await judge_one(judges, "alias", {"expectation": {"text": "x" * 200}})
    assert answer.verdict == "error"
    assert answer.reason.startswith("judge input over budget")
    assert str(len(json.dumps(
        {"expectation": {"text": "x" * 200}}).encode())) in answer.reason
    assert judges.calls == []


class _RaisingJudges(_RecordingJudges):
    async def call(self, model, instruction, payload):
        raise RuntimeError("the generated upstream refused the call")


async def test_a_judge_failure_names_its_error_type_and_message():
    answer = await judge_one(_RaisingJudges(), "alias", {})
    assert answer.verdict == "error"
    assert "RuntimeError" in answer.reason
    assert "the generated upstream refused the call" in answer.reason
