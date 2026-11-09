"""Generated controls for the decision diagnostic; no model or replay."""

from __future__ import annotations

import pytest

from nora.subject.replay_diagnosis import diagnose


def scripted_judge(statement, events):
    """The wire shape varies with the record, including invalid responses."""
    if statement.get("wrong_shape"):
        return [{"verdict": "wrong protocol"}]
    reason = next(e for e in events if e["kind"] == "model_call")
    text = reason["reasoning"]
    if "not memory" in text:
        return {
            "observation": "rejected_as_not_memory",
            "event_ids": [reason["event_id"]],
            "reason": text,
            "quote": "not memory",
        }
    if "uncited" in text:
        return {
            "observation": "uncited_detail",
            "event_ids": [reason["event_id"]],
            "reason": "detail from uncited row",
        }
    terminal = [e for e in events if e.get("name") == "nora.session.terminal"]
    if terminal:
        return {
            "observation": "named_bound",
            "event_ids": [terminal[0]["event_id"]],
            "reason": "session reached token bound",
        }
    proposals = [e for e in events if e["kind"] == "tool_finished"]
    if any(p["result"].get("status") == "saved" for p in proposals):
        return {"observation": "other", "event_ids": [p["event_id"] for p in proposals],
                "reason": "the refused proposal was retried and saved"}
    if proposals:
        return {
            "observation": "refused_not_retried",
            "event_ids": [proposals[0]["event_id"]],
            "reason": "proposal refused",
        }
    return {
        "observation": "not_mentioned",
        "event_ids": [reason["event_id"]],
        "reason": "not present in emitted reasoning",
    }


@pytest.mark.parametrize(
    ("control", "expected"),
    [
        ("missing", "insufficient_evidence"),
        ("redacted", "insufficient_evidence"),
        ("unavailable", "insufficient_evidence"),
        ("rejection", "rejected_as_not_memory"),
        ("refusal", "refused_not_retried"),
        ("retry", "other"),
        ("budget", "named_bound"),
        ("uncited", "uncited_detail"),
        ("unmentioned", "not_mentioned"),
        ("wrong_shape", "insufficient_evidence"),
    ],
)
def test_decision_controls(control, expected):
    events = [
        {
            "kind": "model_call",
            "event_id": "e2",
            "reasoning_availability": {
                "missing": "absent",
                "redacted": "redacted",
                "unavailable": "capture_unavailable",
            }.get(control, "observed"),
            "reasoning": {"rejection": "not memory", "uncited": "took uncited detail"}.get(
                control, "other"
            ),
        },
    ]
    if control in {"refusal", "retry"}:
        events.append(
            {
                "kind": "tool_finished",
                "event_id": "e3",
                "tool": {"name": "memory_propose"},
                "result": {"status": "refused"},
            }
        )
    if control == "retry":
        events.append(
            {
                "kind": "tool_finished",
                "event_id": "e4",
                "tool": {"name": "memory_propose"},
                "result": {"status": "saved"},
            }
        )
    if control == "budget":
        events.append(
            {
                "kind": "event",
                "name": "nora.session.terminal",
                "event_id": "e3",
                "attributes": {"body": '{"bound":"tokens"}'},
            }
        )
    statement = {
        "kind": "unsupported" if control == "uncited" else "missed",
        "wrong_shape": control == "wrong_shape",
    }
    result = diagnose(statement, {"events": events}, scripted_judge(statement, events))
    assert result["observation"] == expected


def test_unknown_event_cannot_ground_a_diagnostic():
    events = [
        {"kind": "model_call", "event_id": "e1", "reasoning_availability": "observed",
         "reasoning": "generated"}
    ]
    assert (
        diagnose(
            {"kind": "missed"},
            {"events": events},
            {"observation": "not_mentioned", "event_ids": ["nonexistent"], "reason": "claim"},
        )["observation"]
        == "insufficient_evidence"
    )


@pytest.mark.parametrize("mode", [
    "live", "rescore", "legacy", "tampered", "http400", "parse", "oversized", "absent",
    "missing_reason", "uncited",
])
async def test_decision_resolves_fingerprinted_writer_events(tmp_path, monkeypatch, mode):
    import json
    from tempfile import TemporaryFile
    from types import SimpleNamespace

    from opentelemetry import trace

    from evals.backbone import _prefill_capture
    from nora.subject.eval_evidence import RunEvidence, read_events, validate_events
    from scripts import memory_graph_spike as spike
    _prefill_capture()
    source_root = tmp_path / "source"
    source_root.mkdir(mode=0o700)
    fact = {"fact": "Generated fact", "quote": "Generated fact", "location": "1",
            "reason": "generated omission"}
    agenda = '<item kind="message"><m n="1">Generated fact</m></item>'
    with TemporaryFile() as lock:
        source = RunEvidence(run_id="spike", metadata={}, lock_fd=lock.fileno(),
                             private_root=source_root)
        with source.capture(trace.get_tracer_provider()):
            source.record("event", name="nora.session.started", span_id="generated", attributes={
                "body": json.dumps({"session_id": 1, "system": "generated", "agenda": agenda})})
            source.record("event", name="nora.model.request", span_id="generated", attributes={
                "nora.model.request.body": json.dumps({"messages": [
                    {"role": "system", "content": "generated"},
                    {"role": "user", "content": agenda}]})})
            source.record("transport_started", span_id="generated")
            source.record("transport_response", span_id="generated", source_marker="writer",
                headers={"x-omniroute-model": "generated-writer",
                         "x-omniroute-provider": "generated-writer"},
                response={"choices": [{"message": {"reasoning_content": (
                    "" if mode == "absent" else "not memory" + ("x" * 80_000 if mode == "oversized"
                                                               else ""))}}]})
            source.record("transport_finished", span_id="generated",
                          reasoning_availability="absent" if mode == "absent" else "observed")
            if mode == "uncited":
                tool = {"toolUseId": "generated-read", "name": "load_transcript",
                        "input": {"items": [0]}}
                source.record("tool_started", span_id="generated", tool=tool)
                source.record("tool_finished", span_id="generated", tool=tool,
                              result={"content": [{"text": "Generated context reply_to=2"}]})
                saved = {"what": "Generated fact", "cite": [{"source": 1}]}
                proposal = {"toolUseId": "generated-save", "name": "memory_propose",
                            "input": {"items": [0], "statements": [saved]}}
                source.record("tool_started", span_id="generated", tool=proposal)
                source.record("tool_finished", span_id="generated", tool=proposal,
                              result={"content": [{"json": {"statements": [
                                  {"index": 0, "status": "saved"}]}}]})
            source.record("event", name="nora.session.terminal", span_id="generated", attributes={
                "body": json.dumps({"session_id": 1})})
            writer_ids = [e["event_id"] for e in read_events(source.path)
                          if e.get("span_id") == "generated"]
            if mode == "uncited":
                source.record("judge_verdict", verdict="unsupported", reason="scoped",
                              session_event_ids=writer_ids, statement=saved)
            else:
                source.record("judge_verdict", verdict="missed", reason="scoped",
                              session_event_ids=writer_ids,
                              output={"missed": [fact], "context_only": []})
        assert validate_events(read_events(source.path))["status"] == "PASS"
        target = source_root
        if mode not in {"live", "uncited"}:
            rescored_root = tmp_path / "rescored"
            rescored_root.mkdir(mode=0o700)
            rescored = RunEvidence(run_id="rescore", metadata={}, lock_fd=lock.fileno(),
                                   private_root=rescored_root)

            async def score(*args):
                return json.dumps({"missed": [fact], "context_only": []})

            with rescored.capture(trace.get_tracer_provider()):
                await spike.rescore_facts(source_root, SimpleNamespace(
                    models=["generated"], call=score, served={}))
            scored_events = read_events(rescored.path)
            verdict = next(e for e in scored_events if e["kind"] == "judge_verdict")
            assert verdict["session_event_ids"] == writer_ids
            assert verdict["source_sha256"] == spike.fingerprint(source.path / "events.jsonl")
            target = rescored_root
            if mode == "legacy":
                for e in scored_events:
                    if e["kind"] == "rescore_source":
                        e.pop("path")
                    if e["kind"] == "judge_verdict":
                        e.pop("source_sha256")
                        e["session_event_ids"] = []
                (rescored.path / "events.jsonl").write_text(
                    "".join(json.dumps(e) + "\n" for e in scored_events))
                target = rescored.path
            if mode == "tampered":
                rows = read_events(source.path)
                with (source.path / "events.jsonl").open("a") as output:
                    output.write(json.dumps({"kind": "changed", "order": len(rows) + 1,
                                             "event_id": f"e{len(rows) + 1}"}) + "\n")
        calls = []

        async def diagnose(model, instruction, payload):
            calls.append(payload)
            if mode == "oversized":
                raise AssertionError("oversized trajectory was sent")
            assert "session_event_ids" not in payload["statement"]
            trajectory = payload["trajectory"]
            assert trajectory["agenda_items"] and trajectory["cited_rows"]
            assert all(e["event_id"] in writer_ids for e in trajectory["events"])
            reason = next(e for e in trajectory["events"] if e["kind"] == "model_call")
            assert reason["reasoning"] == "not memory"
            if mode == "http400":
                import httpx
                raise httpx.HTTPStatusError("generated context overflow",
                    request=httpx.Request("POST", "https://generated.invalid/v1"),
                    response=httpx.Response(400))
            if mode == "parse":
                return '{"observation":'
            if mode == "missing_reason":
                return '{"observation":"not_mentioned","event_ids":[]}'
            if mode == "uncited":
                row_id = trajectory["cited_rows"]["1"]["event_id"]
                read_id = next(e["event_id"] for e in trajectory["events"]
                               if e["kind"] == "tool_finished")
                return json.dumps({"observation": "uncited_detail", "event_ids": [row_id, read_id],
                                   "reason": "the detail comes from the transcript reply_to"})
            return json.dumps({"observation": "rejected_as_not_memory", "quote": "not memory",
                "event_ids": [reason["event_id"]], "reason": "generated recorded rejection"})

        monkeypatch.setattr(spike, "Judges", lambda *args: SimpleNamespace(
            models=["generated"], call=diagnose))
        monkeypatch.setattr(spike, "load", lambda *args: None)
        output_root = tmp_path / "diagnostic"
        result = await spike.run_diagnostic(SimpleNamespace(output=output_root,
            decision_judge=target, config=tmp_path / "generated.toml",
            judge_a="generated-a", judge_b="generated-b"))
    failed = mode in {"tampered", "http400", "parse", "oversized", "missing_reason"}
    assert spike.report_exit(result) == (2 if failed else 0)
    if mode == "tampered":
        assert result["status"] == result["evidence"]["status"] == "ERROR"
        assert calls == []
        return
    events = read_events(next(output_root.glob("diagnostic-*")))
    diagnostic = next(e for e in events if e["kind"] == "decision_diagnostic")
    if failed:
        assert result["status"] == result["evidence"]["status"] == "ERROR"
        assert result["judge_unavailable"] == 1 and result["insufficient_evidence"] == 0
        assert diagnostic["result"]["observation"] == "judge_unavailable"
        assert diagnostic["result"]["event_ids"] == []
        assert any(e["kind"] == "judge_error" for e in events)
        if mode == "oversized":
            assert calls == [] and result["input_truncated"] == 1
            assert "truncated" in result["evidence"]["causes"]
            supplied = next(e for e in events if e["kind"] == "decision_input")
            assert supplied["truncated"] and supplied["input_bytes"] > supplied["max_input_bytes"]
        else:
            assert len(calls) == 1
    else:
        assert result["status"] == "REPORTED" and result["evidence"]["status"] == "PASS"
        assert result["statements"] == 1 and result["judge_unavailable"] == 0
        if mode == "absent":
            assert calls == [] and result["insufficient_evidence"] == 1
        else:
            assert len(calls) == 1 and result["insufficient_evidence"] == 0
            expected = "uncited_detail" if mode == "uncited" else "rejected_as_not_memory"
            assert diagnostic["result"]["observation"] == expected
            if mode == "uncited":
                output = next(e["output"] for e in events if e["kind"] == "judge_verdict")
                assert diagnostic["result"]["event_ids"] == output["event_ids"]
                assert diagnostic["result"]["reason"] == output["reason"]


def generated_trajectory(*, later_absent=False, mixed=False, chunks=3, text="not memory",
                         failed_proposal=False):
    """Generated recorded request, streamed call, saved proposal and optional unrelated call."""
    import json
    events = []

    def emit(kind, **payload):
        events.append({"kind": kind, "event_id": f"e{len(events) + 1}", **payload})

    agenda = '<item index="0" kind="message"><m n="1">Generated fact</m></item>'
    emit("event", name="nora.session.started", attributes={"body": json.dumps({"session_id": 1})})
    emit("event", name="nora.model.request", attributes={"nora.model.request.body": json.dumps({
        "messages": [{"role": "system", "content": (
            '<item index="0"><m n="99">System example</m></item>')},
                     {"role": "user", "content": agenda}]})})
    statement = {"what": "Generated fact", "cite": [{"source": 1}]}
    arguments = json.dumps({"items": [0], "statements": [statement]})
    emit("transport_started", attempt_id="generated-one")
    for index in range(chunks):
        emit("transport_response", attempt_id="generated-one", response={"choices": [{
            "delta": {"reasoning_content": text if index == 0 else "x"}}]})
    if failed_proposal:
        emit("transport_response", attempt_id="generated-one", response={"choices": [{"delta": {
            "tool_calls": [{"index": 0, "id": "failed-one", "function": {
                "name": "memory_propose", "arguments": arguments}}]}}]})
    tool_index = int(failed_proposal)
    emit("transport_response", attempt_id="generated-one", response={"choices": [{"delta": {
        "content": "Generated assistant text", "tool_calls": [{"index": tool_index,
        "id": "proposal-one", "function": {"name": "memory_pro", "arguments": arguments[:10]}}]}}]})
    emit("transport_response", attempt_id="generated-one", response={"choices": [{"delta": {
        "tool_calls": [{"index": tool_index, "function": {
            "name": "pose", "arguments": arguments[10:]}}]},
        "finish_reason": "tool_calls"}]})
    emit("transport_finished", attempt_id="generated-one", reasoning_availability="observed")

    def absent():
        emit("transport_started", attempt_id="generated-two")
        emit("transport_response", attempt_id="generated-two", response={"choices": [{
            "message": {"content": "Generated other-item work"}}]})
        emit("transport_finished", attempt_id="generated-two", reasoning_availability="absent")

    if mixed:
        absent()
    statement = {"what": "Generated fact", "cite": [{"source": 1}]}
    use = {"toolUseId": "proposal-one", "name": "memory_propose", "input": {
        "items": [0], "statements": [statement]}}
    if failed_proposal:
        failed = {**use, "toolUseId": "failed-one"}
        emit("tool_started", tool=failed)
        emit("tool_finished", tool=failed, result={"status": "error", "content": [
            {"text": "Generated failed proposal"}]})
    emit("tool_started", tool=use)
    emit("event", name="nora.proposal.sources", attributes={"body": json.dumps({
        "1": {"text": "Generated fact"}})})
    emit("tool_finished", tool=use, result={"content": [{"json": {"statements": [
        {"index": 0, "status": "saved"}]}}]})
    if later_absent:
        absent()
    emit("event", name="nora.session.terminal", attributes={"body": '{"bound":null}'})
    return {"kind": "unsupported", "statement": statement}, events


@pytest.mark.parametrize("structured", [False, True])
def test_missed_diagnostic_uses_source_ids_not_item_numbers_or_timestamps(structured):
    import json

    from nora.subject.replay_diagnosis import compact_trajectory

    _, events = generated_trajectory()
    request = next(e for e in events if e.get("name") == "nora.model.request")
    request["attributes"]["nora.model.request.body"] = json.dumps({"messages": [{
        "role": "user", "content": (
            '<item index="0"><m n="1">Generated fact</m></item>'
            '<item index="1"><m n="12">Generated other</m></item>'),
    }]})
    statement = {"kind": "missed", "location": "Agenda item 0, NEW source row 1 at 12:34"}
    if structured:
        statement["sources"] = [1]
    trajectory = compact_trajectory(statement, events)
    assert [i["index"] for i in trajectory["agenda_items"]] == [0]
    assert set(trajectory["cited_rows"]) == {"1"}


def test_reaction_context_cannot_replace_the_diagnostic_cited_message():
    import json

    from nora.subject.replay_diagnosis import compact_trajectory

    statement, events = generated_trajectory()
    request = next(e for e in events if e.get("name") == "nora.model.request")
    request["attributes"]["nora.model.request.body"] = json.dumps({"messages": [{
        "role": "user", "content": (
            '<item index="0"><m n="1" person="generated-author">Generated fact</m>'
            '<m n="1" reaction="👍">Generated fact</m></item>'),
    }]})
    trajectory = compact_trajectory(statement, events)
    assert 'person="generated-author"' in trajectory["cited_rows"]["1"]["rendered"]
    assert 'reaction="' not in trajectory["cited_rows"]["1"]["rendered"]


def test_compact_statement_scope_excludes_later_absent_call():
    from nora.subject.replay_diagnosis import compact_trajectory
    statement, events = generated_trajectory(later_absent=True, failed_proposal=True)
    trajectory = compact_trajectory(statement, events)
    calls = [e for e in trajectory["events"] if e["kind"] == "model_call"]
    assert len(calls) == 1 and len(trajectory["agenda_items"]) == 1
    assert "System example" not in trajectory["agenda_items"][0]["rendered"]
    assert calls[0]["reasoning"] == "not memoryxx"
    assert calls[0]["assistant_text"] == "Generated assistant text"
    import json
    assert [t["id"] for t in calls[0]["tool_calls"]] == ["failed-one", "proposal-one"]
    proposal = calls[0]["tool_calls"][1]
    assert proposal["name"] == "memory_propose"
    assert json.loads(proposal["arguments"]) == {
        "items": [0], "statements": [{"what": "Generated fact", "cite": [{"source": 1}]}]}
    tools = [e for e in trajectory["events"] if e["kind"] == "tool_finished"]
    assert tools[0]["result"]["status"] == "error" and len(tools) == 2
    assert all(e["kind"] != "transport_response" for e in trajectory["events"])
    assert trajectory["cited_rows"]["1"]["rendered"].endswith("Generated fact</m>")
    verdict = {"observation": "uncited_detail", "event_ids": [calls[0]["event_id"]],
               "reason": "context detail in emitted reasoning"}
    result = diagnose(statement, trajectory, verdict)
    assert result["observation"] == "uncited_detail"


def test_reasoning_observations_use_only_observed_calls():
    from nora.subject.replay_diagnosis import compact_trajectory
    _, events = generated_trajectory(mixed=True)
    statement = {"kind": "missed", "fact": "Generated fact", "location": "1"}
    scoped = compact_trajectory(statement, events)
    calls = [e for e in scoped["events"] if e["kind"] == "model_call"]
    assert [c["reasoning_availability"] for c in calls] == ["observed", "absent"]
    verdict = {"observation": "rejected_as_not_memory", "event_ids": [calls[0]["event_id"]],
               "quote": "not memory", "reason": "explicit rejection"}
    assert diagnose(statement, scoped, verdict)["observation"] == "rejected_as_not_memory"
    verdict["quote"] = "Generated assistant text"
    assert diagnose(statement, scoped, verdict)["observation"] == "insufficient_evidence"
    verdict = {"observation": "not_mentioned", "event_ids": [calls[0]["event_id"]],
               "reason": "not mentioned in this observed call"}
    assert diagnose(statement, scoped, verdict)["observation"] == "not_mentioned"
    verdict["event_ids"] = [calls[1]["event_id"]]
    assert diagnose(statement, scoped, verdict)["observation"] == "insufficient_evidence"


def test_many_chunks_are_assembled_without_raw_chunk_overhead():
    import json

    from nora.subject.replay_diagnosis import MAX_DIAGNOSTIC_INPUT_BYTES, compact_trajectory
    statement, events = generated_trajectory(chunks=18_000)
    trajectory = compact_trajectory(statement, events)
    assert len([e for e in trajectory["events"] if e["kind"] == "model_call"]) == 1
    assert len(trajectory["events"][0]["reasoning"]) == len("not memory") + 17_999
    assert len(json.dumps(trajectory).encode()) < MAX_DIAGNOSTIC_INPUT_BYTES
    assert len(json.dumps(events).encode()) > 2_000_000


@pytest.mark.parametrize("anchor", [
    "agenda", "catalogue", "last_response_event_id", "last_event_id",
])
def test_uncited_detail_accepts_shown_source_and_transcript_ids(anchor):
    import json

    from nora.subject.replay_diagnosis import compact_trajectory
    statement, events = generated_trajectory()
    statement["statement"]["cite"].append({"source": 2})
    catalogue = next(e for e in events if e.get("name") == "nora.proposal.sources")
    catalogue["attributes"]["body"] = json.dumps({"2": {"text": "Generated context"}})
    read = {"kind": "tool_finished", "event_id": "read-context", "tool": {
        "toolUseId": "read-one", "name": "load_transcript", "input": {"items": [0]}},
        "result": {"content": [{"text": '<m n="2" reply_to="1">Generated context</m>'}]}}
    events.insert(-2, read)
    trajectory = compact_trajectory(statement, events)
    call = next(e for e in trajectory["events"] if e["kind"] == "model_call")
    cited_id = (trajectory["agenda_items"][0]["event_id"] if anchor == "agenda" else
                trajectory["cited_rows"]["2"]["event_id"] if anchor == "catalogue" else
                call[anchor])
    verdict = {"observation": "uncited_detail", "event_ids": [cited_id, read["event_id"]],
               "reason": "the detail comes from surrounding context in load_transcript"}
    assert diagnose(statement, trajectory, verdict) == {**verdict, "statement": statement}
    # A raw chunk exists in the source but was not shown as a compact anchor.
    hidden = next(e["event_id"] for e in events if e["kind"] == "transport_response")
    for invalid in (hidden, "not-in-the-source"):
        verdict["event_ids"] = [cited_id, invalid]
        assert diagnose(statement, trajectory, verdict)["observation"] == "insufficient_evidence"


def test_missed_fact_keeps_unsupported_label_out():
    from nora.subject.replay_diagnosis import compact_trajectory
    _, events = generated_trajectory()
    statement = {"kind": "missed", "fact": "Generated fact", "location": "1"}
    trajectory = compact_trajectory(statement, events)
    call = next(e for e in trajectory["events"] if e["kind"] == "model_call")
    verdict = {"observation": "uncited_detail", "event_ids": [call["event_id"]],
               "reason": "generated unsupported-only observation"}
    assert diagnose(statement, trajectory, verdict)["observation"] == "insufficient_evidence"


@pytest.mark.parametrize("shape", ["json", "text"])
@pytest.mark.parametrize("refusal", ["whole", "slot"])
def test_refused_proposal_accepts_other_proposals_in_trajectory(shape, refusal):
    import json

    from nora.subject.replay_diagnosis import compact_trajectory
    _, events = generated_trajectory()
    outcome = {"status": "refused"} if refusal == "whole" else {
        "status": "ok", "statements": [{"index": 0, "status": "saved"},
                                      {"index": 1, "status": "refused", "reason": "schema"}]}
    refused = {"kind": "tool_finished", "event_id": "refused-proposal", "tool": {
        "toolUseId": "refused-one", "name": "memory_propose", "input": {"items": [0],
        "statements": ([{"what": "Generated saved fact"}] if refusal == "slot" else []) + [
            {"what": "Generated other fact"}]}}, "result": {"content": [{
            shape: outcome if shape == "json" else json.dumps(outcome)}]}}
    events.insert(-1, refused)
    statement = {"kind": "missed", "fact": "Generated other fact", "location": "1"}
    trajectory = compact_trajectory(statement, events)
    proposals = [e for e in trajectory["events"] if e["kind"] == "tool_finished"]
    assert len(proposals) == 2
    verdict = {"observation": "refused_not_retried", "event_ids": [
        trajectory["cited_rows"]["1"]["event_id"], refused["event_id"]],
        "reason": "the other fact was refused; the earlier save concerns a different fact"}
    assert diagnose(statement, trajectory, verdict) == {**verdict, "statement": statement}
    verdict["event_ids"] = [proposals[0]["event_id"]]
    assert diagnose(statement, trajectory, verdict)["observation"] == "insufficient_evidence"


@pytest.mark.parametrize("position", ["before", "after", "both"])
def test_compact_trajectory_preserves_self_closing_agenda_items(position):
    import json

    from nora.domain.blocks import render_group
    from nora.subject.replay_diagnosis import _items, compact_trajectory
    statement, events = generated_trajectory()
    event = next(e for e in events if e.get("name") == "nora.model.request")
    request = json.loads(event["attributes"]["nora.model.request.body"])
    normal = request["messages"][1]["content"]
    empty = render_group("item", [("index", "99"), ("kind", "message"),
                                 ("room", "Generated empty room"), ("unreadable", "2")], "")
    agenda = ([empty] if position in {"before", "both"} else []) + [normal]
    agenda += [empty] if position in {"after", "both"} else []
    request["messages"][1]["content"] = "<agenda>" + "\n".join(agenda) + "</agenda>"
    event["attributes"]["nora.model.request.body"] = json.dumps(request)
    parsed = _items(request)
    assert len(parsed) == len(agenda)
    assert all(item["numbers"] == set() for item in parsed if item["index"] == 99)
    trajectory = compact_trajectory(statement, events)
    assert [item["rendered"] for item in trajectory["agenda_items"]] == [normal]
    assert trajectory["cited_rows"]["1"]["rendered"] == '<m n="1">Generated fact</m>'


@pytest.mark.parametrize("observation", [
    "not_mentioned", "rejected_as_not_memory", "mentioned_without_statement",
    "refused_not_retried", "named_bound",
])
def test_missed_only_observations_reject_unsupported_statements(observation):
    events = [
        {"kind": "model_call", "event_id": "call", "reasoning_availability": "observed",
         "reasoning": "not memory"},
        {"kind": "tool_finished", "event_id": "proposal",
         "tool": {"name": "memory_propose"}, "result": {"status": "refused"}},
        {"kind": "event", "event_id": "terminal", "name": "nora.session.terminal",
         "attributes": {"body": '{"bound":"tokens"}'}}]
    anchor = {"refused_not_retried": "proposal", "named_bound": "terminal"}.get(
        observation, "call")
    verdict = {"observation": observation, "event_ids": [anchor],
               "reason": "Generated observed decision", "quote": "not memory"}
    trajectory = {"events": events}
    assert diagnose({"kind": "missed"}, trajectory, verdict)["observation"] == observation
    assert diagnose({"kind": "unsupported"}, trajectory, verdict)[
        "observation"] == "insufficient_evidence"


async def test_decision_deduplicates_verdicts_without_collapsing_sessions(tmp_path, monkeypatch):
    import json
    from tempfile import TemporaryFile
    from types import SimpleNamespace

    from opentelemetry import trace

    from evals.backbone import _prefill_capture
    from nora.subject.eval_evidence import RunEvidence, read_events
    from scripts import memory_graph_spike as spike
    _prefill_capture()
    source_root = tmp_path / "source"
    source_root.mkdir(mode=0o700)
    saved = {"what": "Generated fact", "cite": [{"source": 1}]}
    with TemporaryFile() as lock:
        source = RunEvidence(run_id="spike", metadata={}, lock_fd=lock.fileno(),
                             private_root=source_root)
        with source.capture(trace.get_tracer_provider()):
            for session in range(2):
                span = f"generated-{session}"
                source.record("event", name="nora.session.started", span_id=span,
                              attributes={"body": json.dumps({"session_id": session})})
                agenda = ('<item index="0" kind="message"><m n="1">'
                          'Generated fact with omitted detail</m></item>')
                source.record("event", name="nora.model.request", span_id=span, attributes={
                    "nora.model.request.body": json.dumps({"messages": [
                        {"role": "user", "content": agenda}]})})
                source.record("transport_started", span_id=span)
                source.record("transport_response", span_id=span, response={"choices": [{
                    "message": {"reasoning_content": "Generated context discussion"}}]})
                source.record("transport_finished", span_id=span, reasoning_availability="observed")
                proposal = {"toolUseId": f"save-{session}", "name": "memory_propose",
                            "input": {"items": [0], "statements": [saved]}}
                source.record("tool_started", span_id=span, tool=proposal)
                source.record("tool_finished", span_id=span, tool=proposal, result={"content": [
                    {"json": {"statements": [{"index": 0, "status": "saved"}]}}]})
                source.record("event", name="nora.session.terminal", span_id=span,
                              attributes={"body": "{}"})
                ids = [e["event_id"] for e in read_events(source.path) if e.get("span_id") == span]
                for alias in ("generated-a", "generated-b"):
                    source.record("judge_verdict", alias=alias, verdict="unsupported",
                                  statement=dict(reversed(list(saved.items()))), reason="generated",
                                  session_event_ids=ids)
                    source.record("judge_verdict", alias=alias, verdict="missed", reason="scoped",
                                  session_event_ids=ids, output={"context_only": [], "missed": [{
                                      "fact": "Generated omitted detail", "location": "1",
                                      "quote": "omitted detail", "reason": alias}]})
        calls = []

        async def call(model, instruction, payload):
            calls.append(payload)
            trajectory = payload["trajectory"]
            kind = payload["statement"]["kind"]
            observation = "uncited_detail" if kind == "unsupported" else "not_mentioned"
            assert observation in payload["allowed_observations"]
            anchor = (trajectory["cited_rows"]["1"]["event_id"] if kind == "unsupported" else
                      next(e["event_id"] for e in trajectory["events"]
                           if e["kind"] == "model_call"))
            return json.dumps({"observation": observation, "event_ids": [anchor],
                               "reason": "Generated observed diagnosis"})

        monkeypatch.setattr(spike, "Judges", lambda *args: SimpleNamespace(
            models=["generated-a"], call=call))
        monkeypatch.setattr(spike, "load", lambda *args: None)
        output = tmp_path / "diagnostic"
        result = await spike.run_diagnostic(SimpleNamespace(
            output=output, decision_judge=source_root, config=tmp_path / "generated.toml",
            judge_a="generated-a", judge_b="generated-b"))
    assert result["evidence"]["status"] == "PASS" and result["judge_unavailable"] == 0
    assert result["statements"] == len(calls) == 4
    assert [c["statement"]["kind"] for c in calls] == ["unsupported", "missed"] * 2
    diagnostics = [e["result"] for e in read_events(next(output.glob("diagnostic-*")))
                   if e["kind"] == "decision_diagnostic"]
    scopes = [tuple(d["statement"]["session_event_ids"]) for d in diagnostics]
    assert scopes[0] == scopes[1] and scopes[2] == scopes[3] and scopes[0] != scopes[2]
    assert {d["observation"] for d in diagnostics} == {"uncited_detail", "not_mentioned"}
