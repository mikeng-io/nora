"""The judge's bounded digest: a wake whose trajectory repeats the whole
session context still feeds the judge a digest under the ceiling that
lists every tool call and every diff entry, and an over-budget digest is
ERROR naming its size — never a silent truncation.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

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
from scripts.cognition_loop.trajectory import terminal_response
from tests.integration.subject.evidence_fixtures import judge_trajectory


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
                {"role": "system", "content": "Generated actual system rule"},
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
        "final_response": terminal_response(_responses()),
        "usage": {"tokens": 750_671},
    }


def test_a_multi_megabyte_trajectory_still_yields_a_bounded_digest():
    payload = judge_payload(_expectation(), _diff(), _state(), _trajectory())
    size = len(json.dumps(payload, ensure_ascii=False, default=str).encode())
    assert size < JUDGE_INPUT_MAX_BYTES


def test_a_fat_turn_trajectory_still_yields_a_bounded_digest():
    """A turn's verbatim requests stay in its frozen artifact; the payload
    carries a bounded digest — the same budget a wake payload holds."""
    context = "the whole session context " * 60_000
    # Each call's stack is ~2.4 MB once the tail repeats: undigested the
    # payload is ~21 MB, digested it is tens of KB. The first call's
    # system/developer stay small — they are quoted verbatim as
    # `subject_instructions` by design.
    messages = [{"role": "system", "content": "generated subject rules"},
                {"role": "developer", "content": "generated developer rules"},
                {"role": "user", "content": context},
                *([{"role": "assistant", "content": context},
                   {"role": "tool", "content": context}] * 20)]
    trajectory = {
        "turn": 0, "at_ms": 1_800_000_000_000, "unit": "generated-unit",
        "session_id": None,
        "declared_input": {"text": "generated", "event_id": "e-1",
                           "at_ms": 1_800_000_000_000},
        "model_calls": [{"model": "generated", "stream": True,
                         "tools": [{"function": {"name": "send_reply"}}],
                         "messages": messages}
                        for _ in range(9)],
        "blocks": {},
        "settlement": {"status": "observed", "event": "done", "work": []},
        "turn_outcome": {"status": "delivered", "said": ["generated"]},
        "final_response": {"status": "observed", "text": "generated"},
    }
    expectation = Expectation(
        wake=None, turn=0, polarity="must", kind="behavior",
        text="the turn says the generated thing", check="generated")
    payload = judge_payload(expectation, _diff(), _state(), trajectory)
    size = len(json.dumps(payload, ensure_ascii=False, default=str).encode())
    assert size < JUDGE_INPUT_MAX_BYTES
    calls = payload["model_calls"]
    assert [c["index"] for c in calls] == list(range(9))
    assert calls[0]["tools"] == ["send_reply"]
    assert calls[0]["messages_omitted"] == len(messages) - 3
    # The newest messages are excerpted, never the verbatim stack.
    assert all(len(m["content"]) <= 2000
               for c in calls for m in c["messages"])


def test_the_digest_lists_every_tool_call_and_every_diff_entry():
    payload = judge_payload(_expectation(), _diff(), _state(), _trajectory())
    assert [c["tool"] for c in payload["tool_calls"]] == [
        "note_create", "note_revise", "memo_create", "wake_at"]
    assert [e["id"] for e in payload["diff"]["note"]["created"]] == ["n-1"]
    assert [e["id"] for e in payload["diff"]["note"]["revised"]] == ["n-2"]
    assert [e["id"] for e in payload["diff"]["note"]["retired"]] == ["n-3"]
    # Unchanged is a count only — never the entries themselves.
    assert payload["diff"]["note"]["unchanged"] == 36
    assert payload["final_output"] == "actual terminal answer"
    assert payload["stop_reason"] == "end_turn"
    assert payload["usage"] == {"tokens": 750_671}


def _responses(*, stream=False, text="actual terminal answer"):
    identity = {"invocation_id": "primary", "attempt_id": "primary:0:1"}
    chunks = ([{"delta": {"role": "assistant", "content": text[:4]},
                "finish_reason": None},
               {"delta": {"content": text[4:]}, "finish_reason": "stop"}]
              if stream else [{"message": {"role": "assistant", "content": text},
                               "finish_reason": "stop"}])
    return [
        {**identity, "kind": "invocation_started", "parent_invocation_id": None},
        {**identity, "kind": "call_started"},
        {**identity, "kind": "transport_started"},
        *[{**identity, "kind": "transport_response", "response": {"choices": [c]}}
          for c in chunks],
        {**identity, "kind": "transport_finished"},
        {**identity, "kind": "call_finished", "retry": False},
        {**identity, "kind": "invocation_finished"},
    ]


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("text", ["actual terminal answer", ""])
def test_terminal_response_is_canonical_including_empty_and_streamed_output(stream, text):
    response = terminal_response(_responses(stream=stream, text=text))
    assert response["status"] == "observed" and response["text"] == text
    trajectory = {**_trajectory(), "final_response": response}
    payload = judge_payload(_expectation(), _diff(), _state(), trajectory)
    assert payload["final_output"] == text
    assert payload["final_response"]["attempt_id"] == "primary:0:1"


def test_request_history_never_supplies_missing_or_interrupted_final_response():
    trajectory = _trajectory()
    trajectory.pop("final_response")
    assert judge_payload(_expectation(), _diff(), _state(), trajectory)["final_output"] is None
    for missing in ("transport_finished", "call_finished", "invocation_finished"):
        events = [e for e in _responses(stream=True) if e["kind"] != missing]
        response = terminal_response(events)
        assert response["status"] == "unavailable" and response["text"] is None


def test_nested_invocation_does_not_replace_primary_terminal_response():
    events = _responses()
    nested = [{**e, "invocation_id": "child", "attempt_id": "child:0:1"}
              for e in _responses(text="child answer")]
    nested[0]["parent_invocation_id"] = "primary"
    events[3:3] = nested
    assert terminal_response(events)["text"] == "actual terminal answer"
    # A later root that never settles must not fall back to an earlier success.
    events.append({"kind": "invocation_started", "invocation_id": "another"})
    assert terminal_response(events)["status"] == "unavailable"


def test_tool_side_model_transport_cannot_replace_the_completed_sdk_response():
    events = _responses()
    helper = _responses(text="tool-side helper answer")[2:-2]
    events[-1:-1] = helper
    assert terminal_response(events)["text"] == "actual terminal answer"


def test_judge_distinguishes_domain_unavailability_from_transport_success():
    trajectory = _trajectory()
    trajectory["tool_calls"][0]["result"] = {
        "status": "success", "content": [{"json": {"status": "unavailable"}}]}
    result = judge_payload(_expectation(), _diff(), _state(), trajectory)["tool_calls"][0]["result"]
    assert result["status"] == "unavailable"
    assert result["transport_status"] == "success"


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


def test_a_repeated_lineage_is_sent_once_across_the_whole_digest():
    shared = "Lineage(audiences=('generated-room',))"
    diff = StateDiff(kinds={"note": KindDiff("note", created=tuple(
        Entry("created", i, after={"body": i, "lineage": shared})
        for i in ("a", "b")))})
    state = UnitState(kinds={"note": {
        "a": {"body": "a", "lineage": shared},
        "b": {"body": "b", "lineage": shared},
        "c": {"body": "c"},
    }})
    payload = judge_payload(_expectation(), diff, state, {})
    assert json.dumps(payload, default=str).count(shared) == 1
    assert payload["lineages"] == {"lineage-1": shared}
    assert [e["after"]["lineage"] for e in payload["diff"]["note"]["created"]] == [
        "lineage-1", "lineage-1"]
    assert [e.get("lineage") for e in payload["state_after"].values()] == [
        "lineage-1", "lineage-1", None]


def test_judges_receive_the_recorded_sources_and_clock_once_per_wake():
    trajectory = {**_trajectory(), "wake_id": 22, "at_ms": 1_800_000_000_000,
                  "unit": "generated-unit",
                  "unit_rooms": [["signal", "r1"], ["signal", "r2"]],
                  "blocks": {"agenda": "generated closing fact", "items": [],
                             "stimuli": [{"id": 5}], "system": "not a source"}}
    prior = {"wake_id": 11, "at_ms": 1_799_999_000_000,
             "blocks": {"agenda": "generated opening fact"}}
    payload = judge_payload(_expectation(), _diff(), _state(), trajectory,
                            ((_diff(), prior),))
    assert payload["expectation"]["wake_index"] == 0
    assert payload["at_ms"] == 1_800_000_000_000
    assert payload["wake_id"] == 22
    assert payload["unit"] == "generated-unit"
    assert payload["unit_rooms"] == [["signal", "r1"], ["signal", "r2"]]
    assert payload["source"] == {"agenda": "generated closing fact", "items": [],
                                 "stimuli": [{"id": 5}]}
    assert [w["at_ms"] for w in payload["earlier_wakes"]] == [1_799_999_000_000]
    assert payload["earlier_wakes"][0]["source"]["agenda"] == "generated opening fact"
    # A wake that never captured its rooms shows null — unknown, never empty.
    assert payload["earlier_wakes"][0]["unit_rooms"] is None
    assert json.dumps(payload).count("generated closing fact") == 1
    assert "model_calls" not in payload
    assert "system" not in payload["source"]
    empty = judge_payload(_expectation(), _diff(), _state(), {})
    assert empty["source"] == {"agenda": None, "items": None, "stimuli": None}
    assert empty["unit_rooms"] is None


async def test_a_judge_can_cite_an_emitted_prior_change_but_not_an_unshown_entry():
    from scripts.cognition_loop.judges import judge_expectation

    class Judges:
        def __init__(self, cited):
            self.cited = cited
            self.served = {}

        async def call(self, model, instruction, payload):
            assert payload["earlier_wakes"][0]["diff"]["note"]["created"][0]["id"] == "old"
            self.served[model] = [{"x-omniroute-model": model}]
            return json.dumps({"verdict": "met", "reason": "generated evidence",
                               "entry_ids": [self.cited] if self.cited else []})

    prior = StateDiff(kinds={"note": KindDiff("note", created=(
        Entry("created", "old", after={"body": "generated"}),), unchanged=(
        Entry("unchanged", "unshown", after={"body": "not emitted"}),))})
    args = (_expectation(), StateDiff(kinds={}), UnitState(kinds={}),
            judge_trajectory(),
            ((prior, judge_trajectory()),))
    good = await judge_expectation(Judges("note:old"), ("a", "b"), *args)
    assert good.verdict == "PASS"
    bad = await judge_expectation(Judges("note:unshown"), ("a", "b"), *args)
    assert bad.verdict == "ERROR"
    uncited = await judge_expectation(Judges(None), ("a", "b"), *args)
    assert uncited.verdict == "ERROR"


async def test_a_judge_cites_only_the_ids_the_digest_shows():
    """A changed entry cites freely; an unchanged entry of the expected
    kind cites through `state_after`; another kind's unchanged entry is
    emitted as a count only, so citing it is ERROR."""
    from scripts.cognition_loop.judges import judge_expectation

    class Judges:
        def __init__(self, cited):
            self.cited = cited
            self.served = {}

        async def call(self, model, instruction, payload):
            self.served[model] = [{"x-omniroute-model": model}]
            return json.dumps({"verdict": "met", "reason": "generated evidence",
                               "entry_ids": [self.cited]})

    diff = StateDiff(kinds={
        "note": KindDiff("note", created=(
            Entry("created", "n-1", after={"body": "generated"}),), unchanged=(
            Entry("unchanged", "n-old", after={"body": "kept"}),)),
        "memo": KindDiff("memo", unchanged=(
            Entry("unchanged", "m-x", after={"body": "counted only"}),)),
    })
    state = UnitState(kinds={"note": {"n-1": {}, "n-old": {"body": "kept"}},
                             "memo": {"m-x": {"body": "counted only"}}})
    args = (_expectation(), diff, state,
            judge_trajectory(), ())
    created = await judge_expectation(Judges("note:n-1"), ("a", "b"), *args)
    assert created.verdict == "PASS"
    via_state = await judge_expectation(Judges("note:n-old"), ("a", "b"), *args)
    assert via_state.verdict == "PASS"
    unshown = await judge_expectation(Judges("memo:m-x"), ("a", "b"), *args)
    assert unshown.verdict == "ERROR"


def _expanded(payload: dict[str, Any]) -> dict[str, Any]:
    """Each `state_after` entry rebuilt the way the instruction describes."""
    defaults = payload["state_after_defaults"]
    return {i: {**defaults, **b} for i, b in payload["state_after"].items()}


def _roundtrips(payload: dict[str, Any], original: dict[str, Any]) -> None:
    """Expansion restores the original bodies, serialized type included."""
    expanded = _expanded(payload)
    assert expanded.keys() == original.keys()
    for i, body in expanded.items():
        assert json.dumps(body, sort_keys=True, ensure_ascii=False, default=str) == \
            json.dumps(original[i], sort_keys=True, ensure_ascii=False, default=str)


def test_fields_every_state_after_entry_repeats_ride_once_under_defaults():
    entries = {
        i: {"unit": "generated-unit", "tags": ["a", "b"],
            "head": {"rev": 7, "seen": None}, "own": i}
        for i in ("e-1", "e-2", "e-3")
    }
    payload = judge_payload(
        _expectation(), _diff(), UnitState(kinds={"note": entries}), {})
    assert payload["state_after_defaults"] == {
        "unit": "generated-unit", "tags": ["a", "b"],
        "head": {"rev": 7, "seen": None}}
    assert [set(b) for b in payload["state_after"].values()] == [{"own"}] * 3
    _roundtrips(payload, entries)


def test_defaults_preserve_absent_against_null_and_bool_against_int():
    entries = {
        "a": {"shared": "x", "gap": None, "flag": True},
        "b": {"shared": "x", "flag": 1},
    }
    payload = judge_payload(
        _expectation(), _diff(), UnitState(kinds={"note": entries}), {})
    # `gap` is absent from b — never hoisted, never materialized as null;
    # `flag` serializes differently (`true` vs `1`) — stays in each body.
    assert payload["state_after_defaults"] == {"shared": "x"}
    assert payload["state_after"]["a"] == {"gap": None, "flag": True}
    assert payload["state_after"]["b"] == {"flag": 1}
    _roundtrips(payload, entries)
    assert "gap" not in _expanded(payload)["b"]


def test_a_lineage_every_entry_shares_is_named_once_then_hoisted():
    shared = "Lineage(audiences=('generated-room',))"
    entries = {i: {"lineage": shared, "own": i} for i in ("a", "b")}
    payload = judge_payload(
        _expectation(), _diff(), UnitState(kinds={"note": entries}), {})
    assert payload["lineages"] == {"lineage-1": shared}
    assert payload["state_after_defaults"] == {"lineage": "lineage-1"}
    _roundtrips(payload, {
        i: {"lineage": "lineage-1", "own": i} for i in ("a", "b")})


def test_state_after_defaults_stays_empty_for_one_entry_or_none():
    state = UnitState(kinds={"note": {"only": {"a": 1}}})
    payload = judge_payload(_expectation(), _diff(), state, {})
    assert payload["state_after_defaults"] == {}
    assert payload["state_after"] == {"only": {"a": 1}}
    empty = judge_payload(_expectation(), _diff(), UnitState(kinds={}), {})
    assert empty["state_after_defaults"] == {}
    assert empty["state_after"] == {}


def _sized(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, default=str).encode())


async def test_repeated_fields_bring_an_over_budget_state_back_under_the_ceiling():
    big = "the field every profile repeats " * 4_000
    entries = {f"e-{i}": {"blob": big, "own": i} for i in range(30)}
    state = UnitState(kinds={"note": entries})
    payload = judge_payload(_expectation(), _diff(), state, {})
    assert _sized(payload) < JUDGE_INPUT_MAX_BYTES
    # Without defaults the same evidence is over the ceiling — the hoist is
    # what lets it through.
    uncompressed = {**payload, "state_after": _expanded(payload),
                    "state_after_defaults": {}}
    assert _sized(uncompressed) > JUDGE_INPUT_MAX_BYTES
    judges = _RecordingJudges()
    judges.served["alias"] = [{"x-omniroute-model": "served-model"}]
    answer = await judge_one(judges, "alias", payload)
    assert answer.verdict == "met"
    assert judges.calls == [payload]


async def test_a_digest_still_over_budget_after_defaults_is_error_unseen():
    entries = {
        f"e-{i}": {"blob": f"distinct field {i} " * 2_000} for i in range(40)
    }
    state = UnitState(kinds={"note": entries})
    payload = judge_payload(_expectation(), _diff(), state, {})
    assert payload["state_after_defaults"] == {}
    assert _sized(payload) > JUDGE_INPUT_MAX_BYTES
    judges = _RecordingJudges()
    answer = await judge_one(judges, "alias", payload)
    assert answer.verdict == "error"
    assert "over budget" in answer.reason
    assert judges.calls == []


async def test_a_citation_still_names_a_state_after_entry_under_defaults():
    from scripts.cognition_loop.judges import judge_expectation

    class Judges:
        def __init__(self):
            self.served = {}

        async def call(self, model, instruction, payload):
            self.served[model] = [{"x-omniroute-model": model}]
            return json.dumps({"verdict": "met", "reason": "generated",
                               "entry_ids": ["note:e-2"]})

    entries = {f"e-{i}": {"shared": "v", "own": i} for i in (1, 2, 3)}
    state = UnitState(kinds={"note": entries})
    verdict = await judge_expectation(
        Judges(), ("a", "b"), _expectation(), StateDiff(kinds={}), state,
        judge_trajectory(), ())
    assert verdict.verdict == "PASS"


@pytest.mark.parametrize("missing", ["current", "earlier", "items", "tools"])
async def test_missing_lifecycle_evidence_stops_before_either_judge_call(missing):
    from scripts.cognition_loop.judges import judge_expectation

    class Judges:
        def __init__(self):
            self.served, self.calls = {}, []

        async def call(self, model, instruction, payload):
            self.calls.append(payload)
            self.served[model] = [{"x-omniroute-model": model}]
            return json.dumps({"verdict": "met", "reason": "Generated absence control",
                               "entry_ids": []})

    observed = judge_trajectory()
    judges = Judges()
    expectation = _expectation()
    diff, state = StateDiff(kinds={}), UnitState(kinds={})
    good = await judge_expectation(judges, ("a", "b"), expectation, diff, state,
                                   observed, ((diff, observed),))
    assert good.verdict == "PASS" and len(judges.calls) == 2
    judges.calls.clear()
    # Every other gate (instructions, session) stays observed: only the
    # lifecycle projection is damaged, or the missing-evidence refusal is
    # not this test's to claim.
    broken = {**observed, "effects": {}} if missing in ("current", "earlier") else {
        **observed, "effects": {
            key: value for key, value in observed["effects"].items() if key != missing}}
    bad = await judge_expectation(judges, ("a", "b"), expectation, diff, state,
        observed if missing == "earlier" else broken,
        ((diff, broken if missing == "earlier" else observed),))
    assert bad.verdict == "ERROR"
    assert judges.calls == []
    for answer in bad.answers:
        assert answer.reason.startswith(
            "required journey evidence unavailable:"), answer.reason


def test_instruction_evidence_uses_the_first_actual_request_without_assembly_fallback():
    trajectory = _trajectory()
    trajectory["blocks"] = {"system": "Untransmitted assembled rule"}
    messages = [{"role": "system", "content": "Actual transmitted rule, punctuation retained."},
                {"role": "developer", "content": [{"type": "text", "text": "Actual second role"}]},
                {"role": "user", "content": "Generated source"}]
    trajectory["model_calls"] = [{"messages": messages}, {
        "messages": [{"role": "system", "content": "Later completion rule"}]}]
    payload = judge_payload(_expectation(), _diff(), _state(), trajectory)
    assert payload["subject_instructions"] == {"status": "observed", "messages": messages[:2]}
    import html

    from scripts.cognition_loop.html import _read

    rendered = _read({"trajectory": trajectory})
    assert html.escape(json.dumps(payload["subject_instructions"], indent=2,
                                  ensure_ascii=False)) in rendered
    for missing in (None, [], [{"messages": None}]):
        trajectory["model_calls"] = missing
        receipt = judge_payload(
            _expectation(), _diff(), _state(), trajectory)["subject_instructions"]
        assert receipt["status"] == "unavailable" and receipt["messages"] is None
    trajectory["model_calls"] = [{"messages": [{"role": "user", "content": "Generated"}]}]
    assert judge_payload(_expectation(), _diff(), _state(), trajectory)["subject_instructions"] == {
        "status": "unavailable", "cause": "subject_instructions_not_captured", "messages": None}
