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
        assert payload["cited_rows"]["1"]["rendered"] == rendered
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


async def test_rescore_generated_run_directory(tmp_path):
    from tempfile import TemporaryFile

    from opentelemetry import trace

    from evals.backbone import _prefill_capture
    from nora.subject.eval_evidence import RunEvidence, read_events, validate_events
    from scripts.memory_graph_spike import freeze, rescore_facts
    _prefill_capture()
    source = tmp_path / "source"
    source.mkdir(mode=0o700)
    with TemporaryFile() as lock:
        original = RunEvidence(run_id="spike", metadata={}, lock_fd=lock.fileno(),
                               private_root=source)
        with original.capture(trace.get_tracer_provider()):
            pass
        freeze(source / "session-0.json", {"inspection": {"new_sources": {
            "1": {"text": "generated fact"}}}, "events": []})
        before = (original.path / "events.jsonl").read_bytes()

        async def call(*args):
            return json.dumps({"missed": [{"fact": "generated", "quote": "generated fact",
                                          "location": "1", "reason": "absent from saved"}],
                               "context_only": []})

        rescored = RunEvidence(run_id="rescore", metadata={}, lock_fd=lock.fileno(),
                               private_root=tmp_path)
        with rescored.capture(trace.get_tracer_provider()):
            result = await rescore_facts(source, SimpleNamespace(models=["generated"],
                                                               call=call, served={}))
    assert result["sessions"] == 1
    assert result["results"][0]["facts_missed"] == 1
    assert (original.path / "events.jsonl").read_bytes() == before
    assert validate_events(read_events(rescored.path))["status"] == "PASS"
    assert any(e["kind"] == "rescore_source" for e in read_events(rescored.path))


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
