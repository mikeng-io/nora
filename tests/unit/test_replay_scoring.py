"""Fact judges see cited rows; context-only facts never enter the misses."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from nora.agent.replay_inspection import compare_inspection
from scripts.memory_graph_spike import smoke_fact_support


@pytest.mark.parametrize("malformed", [False, True])
async def test_scoring_citation_scope_new_rows_and_reasons(malformed):
    calls = []

    async def call(model, instruction, payload):
        calls.append(payload)
        if "statement" in payload:
            assert payload["cited_rows"] == {"1": {"text": "Generated train arrives"}}
            assert "uncited secret detail" not in json.dumps(payload["cited_rows"])
            return json.dumps({"verdict": "unsupported", **(
                {} if malformed else {"reason": "detail absent from cited row"})})
        assert "passing moments" in instruction
        assert "no durability" in instruction.lower()
        return json.dumps({"missed": [{"fact": "moment", "quote": "train arrives",
                                       "location": "1", "reason": "new source"}],
                           "context_only": [{"fact": "older", "quote": "old context",
                                             "location": "9", "reason": "older row"}]})

    judges = SimpleNamespace(models=["generated-judge"], call=call)
    inspection = {"new_sources": {"1": {"text": "Generated train arrives"}}}
    exchanges = [{"name": "memory_propose", "input": {"statements": [
        {"what": "uncited secret detail", "cite": [{"source": 1}]}]},
        "source_rows": {"1": {"text": "Generated train arrives"},
                        "2": {"text": "uncited secret detail"}},
        "result": {"statements": [{"index": 0, "status": "saved"}]}}]
    result = await smoke_fact_support(judges, inspection, exchanges)
    assert result["facts_judge_errors"] == int(malformed)
    assert result["facts_unsupported"] == int(not malformed)
    assert result["facts_missed"] == 1
    assert result["facts_context_only"] == 1
    assert len(calls) == 2


def test_inspection_compares_only_normalized_system_and_agenda():
    inspection = {"system": "Generated — system", "agenda": "Generated — agenda"}
    request = {"messages": [{"role": "system", "content": "Generated, system"},
                            {"role": "user", "content": [{"type": "text",
                                                          "text": "Generated, agenda"}]}],
               "tools": [{"unrelated": "tool schema"}], "temperature": 0.5}
    assert compare_inspection(inspection, request)
    request["messages"][0]["content"] = "Wrong system"
    assert not compare_inspection(inspection, request)


def test_nested_entity_attributes_keep_their_own_cited_rows():
    from scripts.memory_graph_spike import cited_source_numbers
    assert cited_source_numbers({"cite": [{"source": 1}], "subject": {"new": {
        "attributes": {"name": {"value": "generated", "cite": [{"source": 2}]}}}}}) == {"1", "2"}


@pytest.mark.parametrize("kind", ["call_started", "call_finished"])
def test_unmatched_call_is_the_only_error(kind):
    from nora.subject.eval_evidence import validate_events
    record = [{"kind": kind, "invocation_id": "generated", "attempt_id": "generated:0:1"},
              {"kind": "run_finished"}]
    receipt = validate_events(record)
    assert receipt["status"] == "ERROR"
    assert receipt["causes"] == ["unmatched_event"]


async def test_support_uses_recorded_author_and_room():
    from scripts.memory_graph_spike import evidence_exchanges
    rendered = ('<item index="1" room="Generated room"><m n="1" from="Aster" '
                'person="aster" at="now">train arrives</m></item>')
    events = [
        {"kind": "event", "name": "nora.model.request", "attributes": {
            "nora.model.request.body": json.dumps({"messages": [{"content": rendered}]})}},
        {"kind": "event", "name": "nora.proposal.sources", "span_id": "generated",
         "attributes": {"body": json.dumps({"1": {"text": "train arrives"}})}},
        {"kind": "tool_finished", "span_id": "generated", "tool": {
            "name": "memory_propose", "input": {"statements": [
                {"what": "aster", "cite": [{"source": 1}]},
                {"what": "birch", "cite": [{"source": 1}]}]}},
         "result": {"content": [{"json": {"statements": [
             {"index": 0, "status": "saved"}, {"index": 1, "status": "saved"}]}}]}}]

    async def call(model, instruction, payload):
        if "statement" not in payload:
            return '{"missed": [], "context_only": []}'
        assert payload["cited_rows"]["1"]["rendered"] == rendered.split(">", 1)[1][:-7]
        assert payload["cited_rows"]["1"]["room"] == 'room="Generated room"'
        verdict = "supported" if payload["statement"]["what"] == "aster" else "unsupported"
        return json.dumps({"verdict": verdict, "reason": "compare recorded person attribute"})

    result = await smoke_fact_support(SimpleNamespace(models=["generated"], call=call),
                                      {}, evidence_exchanges(events))
    assert result["facts_supported"] == result["facts_unsupported"] == 1
    assert result["facts_judge_errors"] == 0


@pytest.mark.parametrize("wrapper", ["{}", "```json\n{}\n```", "{}\nGenerated explanation",
                                      "```json\n{}\n```\nGenerated explanation"])
@pytest.mark.parametrize("outside", [False, True])
async def test_miss_is_decided_without_fatal_error(tmp_path, wrapper, outside):
    from tempfile import TemporaryFile

    from opentelemetry import trace

    from evals.backbone import _prefill_capture
    from nora.subject.eval_evidence import RunEvidence, read_events, validate_events
    _prefill_capture()
    entry = {"fact": "generated fact", "quote": "absent" if outside else "first / second",
             "location": "1 / 2", "reason": "generated reason"}

    async def call(*args):
        return wrapper.format(json.dumps({"missed": [entry], "context_only": []}))

    with TemporaryFile() as lock:
        evidence = RunEvidence(run_id="generated", metadata={}, lock_fd=lock.fileno(),
                               private_root=tmp_path)
        with evidence.capture(trace.get_tracer_provider()):
            result = await smoke_fact_support(SimpleNamespace(models=["generated"], call=call),
                {"new_sources": {"1": {"text": "first\n "}, "2": {"text": " second"}}}, [])
        events = read_events(evidence.path)
    assert result["facts_missed"] == int(not outside)
    assert result["facts_judge_errors"] == 0
    assert validate_events(events)["status"] == "PASS"
    assert next(e for e in events if e["kind"] == "fact_verdict")["verdict"] == (
        "miss_outside_new_sources" if outside else "missed")


@pytest.mark.parametrize("raw", [
    "", '{"missed":[{"fact":"x","quote":"x","location":"1"}],"context_only":[]}',
])
async def test_empty_output_or_missing_reason_is_fatal(tmp_path, raw):
    from tempfile import TemporaryFile

    from opentelemetry import trace

    from evals.backbone import _prefill_capture
    from nora.subject.eval_evidence import EvidenceError, RunEvidence, read_events, validate_events
    _prefill_capture()

    async def call(*args):
        return raw

    with TemporaryFile() as lock:
        evidence = RunEvidence(run_id="generated", metadata={}, lock_fd=lock.fileno(),
                               private_root=tmp_path)
        with pytest.raises(EvidenceError), evidence.capture(trace.get_tracer_provider()):
            result = await smoke_fact_support(SimpleNamespace(models=["generated"], call=call),
                                              {}, [])
    assert result["facts_judge_errors"] == 1
    assert validate_events(read_events(evidence.path))["status"] == "ERROR"


@pytest.mark.parametrize("disagree", [False, "inspection", "system", "events", "scope"])
async def test_rescore_generated_run_directory(tmp_path, monkeypatch, disagree):
    from tempfile import TemporaryFile

    from opentelemetry import trace

    from evals.backbone import _prefill_capture
    from nora.subject.eval_evidence import EvidenceError, RunEvidence, read_events, validate_events
    from scripts import memory_graph_spike as spike
    _prefill_capture()
    source = tmp_path / "source"
    source.mkdir(mode=0o700)
    calls = []
    with TemporaryFile() as lock:
        original = RunEvidence(run_id="spike", metadata={}, lock_fd=lock.fileno(),
                               private_root=source)
        with pytest.raises(EvidenceError), original.capture(trace.get_tracer_provider()):
            for number in range(2):
                root, child = f"session-{number}", f"tool-{number}"
                def event(name, body, root=root):
                    original.record("event", name=name, span_id=root,
                                    attributes={"body": json.dumps(body)})
                agenda = (f'<item index="0" kind="message" room="Generated room">'
                          f'<m n="{number + 1}" from="Aster" person="aster" at="now">'
                          f'generated fact {number}</m>'
                          '<m n="99" source="message:99">old context</m></item>')
                original.record("span_started", span_id=root, parent_span_id=None)
                event("nora.session.instructions", {"doctrine_sha256": "generated"})
                event("nora.session.started", {"session_id": number, "system": "generated",
                                              "agenda": agenda, "items": []})
                original.record("event", name="nora.model.request", span_id=root, attributes={
                    "nora.model.request.body": json.dumps({"messages": [
                        {"role": "system", "content": "generated"},
                        {"role": "user", "content": agenda}]})})
                original.record("span_started", span_id=child, parent_span_id=root)
                use = {"toolUseId": str(number), "name": "memory_propose", "input": {
                    "statements": [{"what": "generated", "cite": [{"source": number + 1}]}]}}
                original.record("tool_started", span_id=child, tool=use)
                original.record("event", name="nora.proposal.sources", span_id=child,
                    attributes={"body": json.dumps({str(number + 1): {
                        "key": ["message", str(number + 1)], "text": f"generated fact {number}"}})})
                original.record("tool_finished", span_id=child, tool=use, result={
                    "content": [{"json": {"statements": [{"index": 0, "status": "saved"}]}}]})
                original.record("span_finished", span_id=child)
                event("nora.session.terminal", {"session_id": number})
                original.record("span_finished", span_id=root)
                if number == 1:
                    original.record("judge_error", cause="judge_incomplete")
        original_events = read_events(original.path)
        # Historical failed scoring left the second frozen session absent.
        first = spike.recorded_fact_sessions(original_events)[0]
        frozen_inspection = json.loads(json.dumps(first["inspection"]))
        if disagree == "inspection":
            frozen_inspection["agenda"] = "disagrees with recorded request"
        if disagree == "system":
            frozen_inspection["system"] = "disagrees with recorded request"
        if disagree == "scope":
            frozen_inspection["new_sources"] = {}
        frozen_events = json.loads(json.dumps(first["events"]))
        if disagree == "events":
            frozen_events[0]["attributes"]["body"] = json.dumps({
                "session_id": 0, "system": "altered", "agenda": agenda, "items": []})
        spike.freeze(source / "session-0.json", {"inspection": frozen_inspection,
                                               "events": frozen_events})
        assert not (source / "session-1.json").exists()
        assert not any(e["kind"] == "fact_scoring_input" for e in original_events)
        before = (original.path / "events.jsonl").read_bytes()

        async def call(model, instruction, payload):
            calls.append(payload)
            if "statement" in payload:
                return '{"verdict":"supported","reason":"generated evidence"}'
            new_sources = payload["session_input"]["input"]["new_sources"]
            assert len(new_sources) == 1 and "99" not in new_sources
            n = next(iter(new_sources))
            return json.dumps({"missed": [{"fact": "generated", "quote": "generated fact",
                                          "location": n, "reason": "absent from saved"}],
                               "context_only": []})

        monkeypatch.setattr(spike, "Judges", lambda *args: SimpleNamespace(
            models=["generated"], call=call, served={}))
        monkeypatch.setattr(spike, "load", lambda *args: None)
        output = tmp_path / "rescored"
        result = await spike.run_rescore(SimpleNamespace(rescore=source, output=output,
            config=tmp_path / "generated.toml", judge_a="generated-a", judge_b="generated-b"))
        receipt = json.loads((output / "report.json").read_text())
        assert receipt == result
        events = read_events(next(output.glob("rescore-*")))
    assert (original.path / "events.jsonl").read_bytes() == before
    if disagree in {"inspection", "system", "events"}:
        assert result["status"] == "ERROR"
        assert validate_events(events)["status"] == "ERROR"
        assert calls == []
    else:
        assert result["status"] == "REPORTED"
        assert result["sessions"] == 2
        assert len(result["results"]) == 2
        assert [r["facts_supported"] for r in result["results"]] == [1, 1]
        assert [r["facts_missed"] for r in result["results"]] == [1, 1]
        assert validate_events(events)["status"] == "PASS"
        assert len(calls) == 4
        assert any(e["kind"] == "rescore_source" for e in events)
        mismatches = [e for e in events if e["kind"] == "scope_mismatch"]
        if disagree == "scope":
            assert len(mismatches) == 1
            assert mismatches[0]["inspection_rows"] == 0
            assert mismatches[0]["recorded_rows"] == 1
            assert mismatches[0]["recorded_chars"] > mismatches[0]["inspection_chars"]
        else:
            assert mismatches == []


@pytest.mark.parametrize("present", [True, False])
async def test_judge_transport_identity_without_eval_capture(monkeypatch, present):
    import httpx

    from nora.agent.structured import build_structured_model
    from scripts import memory_graph_spike as spike
    headers = {"x-omniroute-model": "generated-served",
               "x-omniroute-provider": "generated-vendor"} if present else {}

    def respond(request):
        assert json.loads(request.content)["model"] == "generated-judge"
        return httpx.Response(200, headers=headers, json={
            "id": "generated", "object": "chat.completion", "created": 1,
            "model": "generated-served", "choices": [{"index": 0, "finish_reason": "stop",
            "message": {"role": "assistant", "content": '{"missed":[],"context_only":[]}'}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4}})

    original = build_structured_model
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as transport:
        def build(**kwargs):
            model = original(**kwargs, http_client=transport)
            model.update_config(stream=False)
            return model
        monkeypatch.setattr(spike, "build_structured_model", build)
        monkeypatch.setattr(spike.Judges, "_endpoint", lambda *args: (
            "https://generated.invalid/v1", "generated-key", "generated-judge"))
        judges = spike.Judges(SimpleNamespace(llm=SimpleNamespace(timeout_seconds=5)),
                              ["generated-judge"], None)
        assert await judges.call("generated-judge", "generated instruction", {})
    assert judges.served["generated-judge"] == [{
        key: headers.get(key) for key in ("x-omniroute-model", "x-omniroute-provider")}]
    judges.served["writer"] = [{"x-omniroute-model": "generated-writer",
                                "x-omniroute-provider": "generated-other-vendor"}]
    assert spike.served_independence(judges.served) == ("independent" if present else "unavailable")


async def test_session_inputs_are_frozen_before_failed_scoring(tmp_path):
    from tempfile import TemporaryFile

    from opentelemetry import trace

    from evals.backbone import _prefill_capture
    from nora.subject.eval_evidence import EvidenceError, RunEvidence, read_events
    from scripts.memory_graph_spike import freeze_fact_session
    _prefill_capture()

    async def call(*args):
        return ""

    with TemporaryFile() as lock:
        evidence = RunEvidence(run_id="generated", metadata={}, lock_fd=lock.fileno(),
                               private_root=tmp_path)
        with pytest.raises(EvidenceError), evidence.capture(trace.get_tracer_provider()):
            freeze_fact_session(tmp_path, 0, {}, {"new_sources": {}}, [])
            await smoke_fact_support(SimpleNamespace(models=["generated"], call=call), {}, [])
    frozen = json.loads((tmp_path / "session-0.json").read_text())
    assert frozen["inspection"] == {"new_sources": {}}
    kinds = [e["kind"] for e in read_events(evidence.path)]
    assert kinds.index("fact_scoring_input") < kinds.index("judge_error")


async def test_negative_social_source_numbers_are_scored():
    from nora.language.cognition_render import rendered_new_sources
    from scripts.memory_graph_spike import evidence_exchanges

    caption = "Generated author likes tea and runs daily"
    agenda = ('<item kind="social" room="Generated room"><m n="-1" '
              'source="social:generated-network:item" person="generated-author" '
              f'from="Generated author" at="now">{caption}</m></item>')
    statement = {"what": "Generated author likes tea", "cite": [{"source": -1}]}
    events = [
        {"kind": "event", "name": "nora.model.request", "attributes": {
            "nora.model.request.body": json.dumps({"messages": [{"content": agenda}]})}},
        {"kind": "event", "name": "nora.proposal.sources", "span_id": "generated",
         "attributes": {"body": json.dumps({"-1": {"text": caption}})}},
        {"kind": "tool_finished", "span_id": "generated", "tool": {
            "name": "memory_propose", "input": {"statements": [statement]}},
         "result": {"content": [{"json": {"statements": [{"index": 0, "status": "saved"}]}}]}}]

    async def call(model, instruction, payload):
        if "statement" in payload:
            row = payload["cited_rows"]["-1"]["rendered"]
            assert 'person="generated-author"' in row
            return '{"verdict":"supported","reason":"generated author and caption"}'
        return json.dumps({"missed": [{"fact": "Generated author runs daily", "quote": "runs daily",
                                      "location": "-1", "reason": "generated omission"}],
                           "context_only": []})

    result = await smoke_fact_support(SimpleNamespace(models=["generated"], call=call),
        {"new_sources": rendered_new_sources(agenda)}, evidence_exchanges(events))
    assert result["facts_supported"] == 1
    assert result["facts_missed"] == 1
    assert result["facts_judge_errors"] == 0
