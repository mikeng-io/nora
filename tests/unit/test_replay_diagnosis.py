"""Generated controls for the decision diagnostic; no model or replay."""

from __future__ import annotations

import pytest

from nora.subject.replay_diagnosis import diagnose


def scripted_judge(statement, events):
    """The wire shape varies with the record, including invalid responses."""
    if statement.get("wrong_shape"):
        return [{"verdict": "wrong protocol"}]
    reason = next(e for e in events if e["kind"] == "transport_response")
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
        ("retry", "insufficient_evidence"),
        ("budget", "named_bound"),
        ("uncited", "uncited_detail"),
        ("unmentioned", "not_mentioned"),
        ("wrong_shape", "insufficient_evidence"),
    ],
)
def test_decision_controls(control, expected):
    events = [
        {
            "kind": "transport_finished",
            "event_id": "e1",
            "reasoning_availability": {
                "missing": "absent",
                "redacted": "redacted",
                "unavailable": "capture_unavailable",
            }.get(control, "observed"),
        },
        {
            "kind": "transport_response",
            "event_id": "e2",
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
    assert diagnose(statement, events, scripted_judge(statement, events))["observation"] == expected


def test_unknown_event_cannot_ground_a_diagnostic():
    events = [
        {"kind": "transport_finished", "event_id": "e1", "reasoning_availability": "observed"}
    ]
    assert (
        diagnose(
            {"kind": "missed"},
            events,
            {"observation": "not_mentioned", "event_ids": ["nonexistent"], "reason": "claim"},
        )["observation"]
        == "insufficient_evidence"
    )


@pytest.mark.parametrize("mode", ["live", "rescore", "legacy", "tampered"])
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
                response={"choices": [{"message": {"reasoning_content": "not memory"}}]})
            source.record("transport_finished", span_id="generated",
                          reasoning_availability="observed")
            source.record("event", name="nora.session.terminal", span_id="generated", attributes={
                "body": json.dumps({"session_id": 1})})
            writer_ids = [e["event_id"] for e in read_events(source.path)
                          if e.get("span_id") == "generated"]
            source.record("judge_verdict", verdict="missed", reason="scoped",
                          session_event_ids=writer_ids,
                          output={"missed": [fact], "context_only": []})
        assert validate_events(read_events(source.path))["status"] == "PASS"
        target = source_root
        if mode != "live":
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
            assert payload["statement"]["session_event_ids"] == writer_ids
            assert {e["event_id"] for e in payload["events"]} == set(writer_ids)
            reason = next(e for e in payload["events"] if e["kind"] == "transport_response")
            assert reason["source_marker"] == "writer"
            return json.dumps({"observation": "rejected_as_not_memory", "quote": "not memory",
                "event_ids": [reason["event_id"]], "reason": "generated recorded rejection"})

        monkeypatch.setattr(spike, "Judges", lambda *args: SimpleNamespace(
            models=["generated"], call=diagnose))
        monkeypatch.setattr(spike, "load", lambda *args: None)
        output_root = tmp_path / "diagnostic"
        result = await spike.run_diagnostic(SimpleNamespace(output=output_root,
            decision_judge=target, config=tmp_path / "generated.toml",
            judge_a="generated-a", judge_b="generated-b"))
    assert spike.report_exit(result) == (2 if mode == "tampered" else 0)
    if mode == "tampered":
        assert result["status"] == result["evidence"]["status"] == "ERROR"
        assert calls == []
    else:
        assert result["status"] == "REPORTED"
        assert result["statements"] == 1 and result["insufficient_evidence"] == 0
        assert result["evidence"]["status"] == "PASS"
        assert len(calls) == 1
        events = read_events(next(output_root.glob("diagnostic-*")))
        diagnostic = next(e for e in events if e["kind"] == "decision_diagnostic")
        assert diagnostic["result"]["observation"] == "rejected_as_not_memory"
