"""Fact judges see cited rows; context-only facts never enter the misses."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from nora.agent.replay_inspection import compare_inspection
from scripts.memory_graph_spike import smoke_fact_support


async def test_both_fact_judges_receive_the_composed_writers_full_criteria():
    from nora.language.comprehend_render import MEMORY_CRITERIA, render_session_system

    system = render_session_system("<!-- cognition-orient -->", "", memory=True)
    assert system.count(MEMORY_CRITERIA) == 1
    assert MEMORY_CRITERIA not in render_session_system("", "", memory=False)
    calls = []

    async def call(model, instruction, payload):
        assert model == "generated-judge"
        assert instruction.count(MEMORY_CRITERIA) == 1
        assert "including all exclusions" in instruction
        assert "Exclude only" not in instruction
        assert payload["session_input"]["input"]["system"] == system
        calls.append("support" if "statement" in payload else "misses")
        if "statement" in payload:
            assert set(payload["cited_rows"]) == {"1"}
            return '{"verdict":"supported","reason":"cited spare controller"}'
        assert payload["saved_statements"] == [statement]
        return '{"missed":[],"context_only":[]}'

    statement = {"what": "has a spare controller", "cite": [{"source": 1}]}
    result = await smoke_fact_support(
        SimpleNamespace(models=["generated-judge"], call=call),
        {"system": system, "new_sources": {"1": {"text": "I have a spare controller"}}},
        [{"name": "memory_propose", "input": {"statements": [statement]},
          "source_rows": {"1": {"text": "I have a spare controller"}},
          "result": {"statements": [{"index": 0, "status": "saved"}]}}],
    )
    assert calls == ["support", "misses"]
    assert result["facts_supported"] == 1
    assert result["facts_judge_errors"] == result["facts_missed"] == 0


@pytest.mark.parametrize("entry, expected", [
    ({"sources": [101], "location": "item 0 at 12:34", "quote": "first"}, True),
    ({"sources": [-1], "location": "item 7", "quote": "image"}, True),
    ({"sources": [999], "location": "row 101", "quote": "first"}, False),
    ({"sources": [101, 999], "location": "row 101", "quote": "first"}, False),
    ({"sources": [], "location": "row 101", "quote": "first"}, False),
    ({"sources": [True], "location": "row 101", "quote": "first"}, False),
    ({"sources": "101", "location": "row 101", "quote": "first"}, False),
    ({"sources": [101], "location": "row 101", "quote": "clipped tail"}, False),
    ({"location": "Agenda item 0, NEW source row 101 at 12:34", "quote": "first"}, True),
    ({"location": "NEW source -1; agenda item 0; social:generated:123", "quote": "image"}, True),
    ({"location": "101 / 102", "quote": "first / second"}, True),
    ({"location": "row 999 and 101; item 0", "quote": "first"}, False),
    ({"location": "item 101 at 12:34", "quote": "first"}, False),
    ({"location": "row 101.2", "quote": "first"}, False),
    ({"location": "row 101foreign", "quote": "first"}, False),
    ({"location": "NEW source rows 101 and 102", "quote": "first / second"}, True),
    ({"location": "sources 101", "quote": "first"}, True),
    ({"location": "row 101 and 2 context rows", "quote": "first"}, True),
    ({"location": "row 101 and 2 rows", "quote": "first"}, True),
    ({"location": "rows 101 and 999", "quote": "first"}, False),
    ({"location": "row 101 and 2", "quote": "first"}, False),
    ({"location": "source row 101-103", "quote": "first"}, False),
])
def test_missed_sources_are_explicit_not_every_number_in_the_locator(entry, expected):
    from scripts.memory_graph_spike import miss_in_new_sources

    sources = {"101": {"text": "first"}, "102": {"text": "second"},
               "-1": {"text": "image"}}
    assert miss_in_new_sources(entry, sources) is expected


@pytest.mark.parametrize("source_ids, expected_misses, expected_errors", [
    ([101], 1, 0), ([999], 0, 0), ([True], 0, 1), ([], 0, 1),
])
async def test_structured_missed_sources_reach_the_real_scoring_boundary(
    source_ids, expected_misses, expected_errors,
):
    async def call(model, instruction, payload):
        assert model == "generated-judge"
        assert set(payload) == {"session_input", "saved_statements"}
        assert payload["saved_statements"] == []
        assert '"sources"' in instruction
        return json.dumps({"missed": [{"fact": "generated first", "quote": "first",
            "sources": source_ids, "location": "item 0, row 101 at 12:34",
            "reason": "generated new fact"}], "context_only": []})

    result = await smoke_fact_support(
        SimpleNamespace(models=["generated-judge"], call=call),
        {"new_sources": {"101": {"text": "first"}}}, [])
    assert result["facts_missed"] == expected_misses
    assert result["facts_judge_errors"] == expected_errors

@pytest.fixture(autouse=True)
def private_unit_export(monkeypatch):
    from scripts import memory_graph_spike
    monkeypatch.setattr(memory_graph_spike, "evaluation_export_for", lambda _: None)



@pytest.mark.parametrize("malformed", [False, True])
async def test_scoring_citation_scope_new_rows_and_reasons(malformed):
    calls = []

    async def call(model, instruction, payload):
        calls.append(payload)
        if "statement" in payload:
            assert payload["session_input"]["input"] is inspection
            assert "<profile" in payload["session_input"]["input"]["agenda"]
            assert "<transcript" in payload["session_input"]["input"]["agenda"]
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
    inspection = {
        "agenda": '<profile of="person:generated">Generated profile</profile>'
                  '<transcript><m n="1" source_id="1">Generated train arrives</m></transcript>',
        "new_sources": {"1": {"text": "Generated train arrives"}},
    }
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
            "nora.model.request.body": json.dumps({"messages": [
                {"role": "user", "content": rendered}]})}},
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
        assert payload["cited_rows"]["1"]["carrier"] == "unavailable"
        verdict = "supported" if payload["statement"]["what"] == "aster" else "unsupported"
        return json.dumps({"verdict": verdict, "reason": "compare recorded person attribute"})

    result = await smoke_fact_support(SimpleNamespace(models=["generated"], call=call),
                                      {}, evidence_exchanges(events))
    assert result["facts_supported"] == result["facts_unsupported"] == 1
    assert result["facts_judge_errors"] == 0


def generated_carrier_events(reads, room, *, ledger_binding=False):
    """A generated shown request and saved proposal whose row 1 is cited."""
    agenda = (f"<reads>{reads}</reads>" if reads else "") + (
        f'<item index="0" kind="message" room="{room}">'
        '<m n="1" from="Aster" person="aster" at="now">generated fact</m></item>')
    ledger = {"text": "generated fact"}
    source_rows = {"1": ledger}
    if ledger_binding:
        ledger["binding"] = "signal:generated-ledger-only"
        source_rows["2"] = {"text": "generated uncited", "binding": "signal:generated-ledger-two"}
    events = [
        {"kind": "event", "name": "nora.model.request", "attributes": {
            "nora.model.request.body": json.dumps({"messages": [
                {"role": "user", "content": agenda}]})}},
        {"kind": "event", "name": "nora.proposal.sources", "span_id": "generated",
         "attributes": {"body": json.dumps(source_rows)}},
        {"kind": "tool_finished", "span_id": "generated", "tool": {
            "name": "memory_propose", "input": {"statements": [
                {"what": "generated", "cite": [{"source": 1}]}]}},
         "result": {"content": [{"json": {"statements": [
             {"index": 0, "status": "saved"}]}}]}}]
    return agenda, events


async def test_support_judge_sees_the_cited_rows_shown_carrier_binding(tmp_path, monkeypatch):
    """The real SDK-formatted judge input carries the shown carrier binding."""
    import html
    import re
    from pathlib import Path

    import httpx

    from evals import grading_contract as binding
    from nora.agent.structured import build_structured_model
    from nora.language.cognition_render import rendered_new_sources
    from scripts import memory_graph_spike as spike
    from tests.integration.test_grading_contract import write_contract

    reads = '<binding agenda_room="signal:generated-room" label="Friends"/>'
    agenda, events = generated_carrier_events(reads, "«Friends»")
    inspection = {"system": "generated", "agenda": agenda,
                  "new_sources": rendered_new_sources(agenda)}
    contract_path, contract_sha = write_contract(
        tmp_path, judges=("generated-judge",),
        cases={"wake": {"input_sha256": binding.digest(
            binding.memory_scenario(inspection)), "expected_output": "",
            "units": [{"id": "unit-a", "claim": "generated claim", "sources": [1]}]}})
    contract = binding.load_contract(contract_path, contract_sha, entrypoint="replay",
                                     judges=("generated-judge",), selection={})
    requests = []

    def respond(request):
        body = json.loads(request.content)
        assert body["model"] == "generated-judge" and body["stream"] is False
        requests.append(body)
        instruction = body["messages"][0]["content"]
        if instruction.startswith("FROZEN_SUPPORT"):
            content = {"verdict": "supported", "reason": "generated support",
                       "eligibility": "eligible", "eligibility_reason": "generated new"}
        else:
            assert instruction.startswith("FROZEN_COVERAGE")
            content = {"coverage": [{"id": "unit-a", "verdict": "covered",
                "reason": "generated reason", "covered_by": {"statements": [0],
                "reads": []}}], "diagnostic_misses": []}
        return generated_completion(body, {}, content=json.dumps(content))

    monkeypatch.setattr(spike.Judges, "_endpoint", lambda *args: (
        "https://generated.invalid/v1", "generated-key", "generated-judge"))
    original = build_structured_model
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as transport:
        monkeypatch.setattr(spike, "build_structured_model", lambda **kwargs: original(
            **kwargs, http_client=transport))
        judges = spike.Judges(SimpleNamespace(llm=SimpleNamespace(timeout_seconds=5)),
                              ["generated-judge"], Path("generated.toml"))
        result = await spike.smoke_fact_support(
            judges, inspection, spike.evidence_exchanges(events),
            contract=contract, reference_key="wake")
    support = next(b for b in requests
                   if b["messages"][0]["content"].startswith("FROZEN_SUPPORT"))
    content = support["messages"][1]["content"]
    if not isinstance(content, str):
        content = "\n".join(p.get("text", "") for p in content or [])
    payload = json.loads(html.unescape(re.search(
        r"<cited_sources[^>]*>\s*(\{.*\})\s*</cited_sources>", content, re.S).group(1)))
    row = payload["cited_rows"]["1"]
    assert row["carrier"] == '<binding agenda_room="signal:generated-room" label="Friends"/>'
    assert row["room"] == 'room="«Friends»"'
    assert result["measurement_version"] == "fixed-reference-v2"
    assert result["facts_supported"] == result["coverage_units"] == 1


@pytest.mark.parametrize("reads,room,expected", [
    ('<binding agenda_room="signal:generated-a" label="Friends"/>'
     '<binding agenda_room="signal:generated-b" label="Friends"/>',
     "«Friends»", "unavailable"),
    ('<binding agenda_room="signal:generated-other" label="Colleagues"/>',
     "«Friends»", "unavailable"),
    ('<binding agenda_room="whatsapp:generated-room" label="Friends"/>',
     "«Friends»", '<binding agenda_room="whatsapp:generated-room" label="Friends"/>'),
], ids=["shared-label", "no-shown-binding", "other-platform"])
def test_an_unresolved_carrier_binding_stays_unavailable(reads, room, expected):
    """A shared or missing label is never guessed; another platform is carried verbatim."""
    from scripts.memory_graph_spike import evidence_exchanges
    _, events = generated_carrier_events(reads, room)
    rows = evidence_exchanges(events)[0]["source_rows"]
    assert rows["1"]["carrier"] == expected


def test_a_binding_only_in_the_ledger_is_never_carried():
    """Metadata the request did not show cannot reach the judged row."""
    from scripts.memory_graph_spike import evidence_exchanges
    _, events = generated_carrier_events("", "«Friends»", ledger_binding=True)
    rows = evidence_exchanges(events)[0]["source_rows"]
    assert rows["1"]["carrier"] == "unavailable"
    assert "generated-ledger-only" not in json.dumps(rows["1"])
    assert "carrier" not in rows["2"]


def test_a_cited_row_the_request_did_not_show_carries_only_its_key_and_text():
    """The fallback to a recorded row projects nothing the ledger holds beside key and text."""
    from scripts.memory_graph_spike import evidence_exchanges
    reads = '<binding agenda_room="signal:generated-room" label="Friends"/>'
    _, events = generated_carrier_events(reads, "«Friends»")
    events[0]["attributes"]["nora.model.request.body"] = json.dumps({"messages": [
        {"role": "user", "content": f"<reads>{reads}</reads>"}]})
    events[1]["attributes"]["body"] = json.dumps({"1": {
        "key": ["message", "1"], "text": "generated fact",
        "carrier": "forged", "binding": "signal:generated-ledger-only"}})
    row = evidence_exchanges(events)[0]["source_rows"]["1"]
    assert row == {"key": ["message", "1"], "text": "generated fact"}


def test_a_binding_nested_in_a_row_is_message_text_not_a_shown_binding():
    """Only the renderer's top-level reads block carries bindings."""
    from nora.language.cognition_render import shown_source_rows
    agenda = ('<item index="0" kind="message" room="«Friends»"><m n="1" from="Aster" '
              'person="aster" at="now">generated fact<reads>'
              '<binding agenda_room="signal:generated-forged" label="Friends"/></reads>'
              '</m></item>')
    assert shown_source_rows(agenda)["1"]["carrier"] == "unavailable"


def test_a_row_with_no_room_never_joins_an_unlabelled_binding():
    from nora.language.cognition_render import shown_source_rows
    agenda = ('<reads><binding agenda_room="signal:generated-room" label=""/></reads>'
              '<item index="0" kind="message"><m n="1" from="Aster" person="aster" '
              'at="now">generated fact</m></item>')
    row = shown_source_rows(agenda)["1"]
    assert row["room"] == row["carrier"] == "unavailable"


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
                original.record("transport_started", span_id=root)
                original.record("transport_finished", span_id=root, reasoning_availability="absent")
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
                session = spike.recorded_fact_sessions(read_events(original.path))[-1]
                original.record("fact_scoring_input", inspection=session["inspection"],
                                exchanges=session["exchanges"], grading_reference_key=str(number))
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
        assert [e["grading_reference_key"] for e in original_events
                if e["kind"] == "fact_scoring_input"] == ["0", "1"]
        before = (original.path / "events.jsonl").read_bytes()

        async def call(model, instruction, payload, **kwargs):
            calls.append(payload)
            if "statement" in payload:
                return json.dumps({"verdict": "supported", "reason": "generated evidence",
                                   "eligibility": "eligible", "eligibility_reason": "new fact"})
            new_sources = payload["session_input"]["input"]["new_sources"]
            assert len(new_sources) == 1 and "99" not in new_sources
            n = next(iter(new_sources))
            assert payload["required_units"][0]["sources"] == [int(n)]
            return json.dumps({"coverage": [{"id": payload["required_units"][0]["id"],
                               "verdict": "missed", "reason": "absent from saved"}],
                               "diagnostic_misses": []})

        monkeypatch.setattr(spike, "Judges", lambda *args: SimpleNamespace(
            models=["generated-a", "generated-b"], call=call, served={}))
        monkeypatch.setattr(spike, "load", lambda *args: None)
        output = tmp_path / "rescored"
        from evals.grading_contract import digest, memory_scenario
        from tests.integration.test_grading_contract import attach_archive_contract
        args = SimpleNamespace(rescore=source, output=output, config=tmp_path / "generated.toml",
                               judge_a="generated-a", judge_b="generated-b")
        args.config.write_text("# generated configuration\n")
        references = {str(i): {"input_sha256": digest(memory_scenario(s["inspection"])),
            "expected_output": "", "units": [{"id": f"fact-{i}", "claim": "generated fact",
                                               "sources": [i + 1]}]}
            for i, s in enumerate(spike.recorded_fact_sessions(original_events))}
        attach_archive_contract(args, tmp_path, "rescore", cases=references)
        result = await spike.run_rescore(args)
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
        assert len(calls) == 8
        verdicts = [e for e in events if e["kind"] == "coverage_verdict"
                    and e["verdict"] == "missed"]
        assert len(verdicts) == 4
        assert verdicts[0]["session_event_ids"] == verdicts[1]["session_event_ids"]
        assert verdicts[2]["session_event_ids"] == verdicts[3]["session_event_ids"]
        assert verdicts[0]["session_event_ids"] != verdicts[2]["session_event_ids"]
        assert all(v["session_event_ids"] and v["source_sha256"] ==
                   spike.fingerprint(original.path / "events.jsonl") for v in verdicts)
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

    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        assert body["model"] == "generated-judge"
        return generated_completion(body, headers if not body["stream"] else {})

    original = build_structured_model
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as transport:
        def build(**kwargs):
            return original(**kwargs, http_client=transport)
        monkeypatch.setattr(spike, "build_structured_model", build)
        monkeypatch.setattr(spike.Judges, "_endpoint", lambda *args: (
            "https://generated.invalid/v1", "generated-key", "generated-judge"))
        judges = spike.Judges(SimpleNamespace(llm=SimpleNamespace(timeout_seconds=5)),
                              ["generated-judge"], None)
        assert await judges.call("generated-judge", "generated instruction", {})
    assert len(requests) == 1 and requests[0]["stream"] is False
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
            "nora.model.request.body": json.dumps({"messages": [
                {"role": "user", "content": agenda}]})}},
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


def generated_completion(body, headers, content='{"missed":[],"context_only":[]}'):
    import httpx

    usage = {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4}
    if not body["stream"]:
        return httpx.Response(200, headers=headers, json={
            "id": "generated", "object": "chat.completion", "created": 1,
            "model": "generated-body-model", "choices": [{"index": 0, "finish_reason": "stop",
            "message": {"role": "assistant", "content": content}}], "usage": usage})
    chunks = [
        {"id": "generated", "object": "chat.completion.chunk", "created": 1,
         "model": "generated-body-model", "choices": [{"index": 0, "finish_reason": None,
         "delta": {"role": "assistant", "content": content}}]},
        {"id": "generated", "object": "chat.completion.chunk", "created": 1,
         "model": "generated-body-model", "choices": [{"index": 0, "finish_reason": "stop",
         "delta": {}}], "usage": usage},
    ]
    data = "".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, headers={"content-type": "text/event-stream", **headers},
                          content=data)


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("present", [False, True])
async def test_raw_identity_capture_for_each_response_shape(tmp_path, stream, present):
    from tempfile import TemporaryFile

    import httpx
    from opentelemetry import trace

    from evals.backbone import _prefill_capture
    from nora.agent.replay_capture import served_identities
    from nora.agent.structured import ask_text, build_structured_model
    from nora.subject.eval_evidence import RunEvidence, read_events
    _prefill_capture()
    headers = {"x-omniroute-model": "generated-served",
               "x-omniroute-provider": "generated-provider"} if present else {}

    def respond(request):
        body = json.loads(request.content)
        assert body["stream"] == stream
        return generated_completion(body, headers)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as transport:
        model = build_structured_model(base_url="https://generated.invalid/v1", api_key="generated",
            model_id="generated-alias", max_tokens=100, temperature=0, http_client=transport)
        model.update_config(stream=stream)
        with TemporaryFile() as lock:
            evidence = RunEvidence(run_id="generated", metadata={}, lock_fd=lock.fileno(),
                                   private_root=tmp_path)
            with evidence.capture(trace.get_tracer_provider()), served_identities() as identities:
                result = await ask_text(model=model, prompt="generated", timeout_seconds=5)
    assert result.content == '{"missed":[],"context_only":[]}'
    assert identities == [{k: headers.get(k) for k in (
        "x-omniroute-model", "x-omniroute-provider")}]
    responses = [e for e in read_events(evidence.path) if e["kind"] == "transport_response"]
    assert responses
    assert all(e["identity_availability"] == ("observed" if present else "unavailable")
               for e in responses)


def generated_agenda_with_empty_items(position):
    from nora.domain.blocks import render_group
    row = ('<m n="7" from="Generated Aster" person="generated-aster" at="now">'
           'Generated &amp; 茶 &lt;item/&gt;</m>')
    normal = render_group("item", [("index", "1"), ("kind", "message"),
                                  ("room", "Generated & room")], row)
    empty = render_group("item", [("index", "0"), ("kind", "message"),
                                 ("room", "Generated empty room"), ("unreadable", "2")], "")
    items = ([empty] if position in {"before", "both"} else []) + [normal]
    items += [empty] if position in {"after", "both"} else []
    return "<agenda>" + "\n".join(items) + "</agenda>", row


@pytest.mark.parametrize("consumer", ["inspection", "exchanges"])
@pytest.mark.parametrize("position", ["before", "after", "both"])
def test_self_closing_agenda_items_keep_sources_and_rooms(consumer, position):
    from nora.language.cognition_render import rendered_new_sources
    from scripts.memory_graph_spike import evidence_exchanges
    agenda, row = generated_agenda_with_empty_items(position)
    if consumer == "inspection":
        assert rendered_new_sources(agenda) == {
            "7": {"key": ["message", "7"], "text": "Generated & 茶 <item/>"}}
    else:
        events = [
            {"kind": "event", "name": "nora.model.request", "attributes": {
                "nora.model.request.body": json.dumps({"messages": [
                    {"role": "user", "content": agenda}]})}},
            {"kind": "event", "name": "nora.proposal.sources", "attributes": {
                "body": json.dumps({"7": {"text": "Generated & 茶 <item/>"}})}},
            {"kind": "tool_finished", "tool": {"name": "memory_propose", "input": {}},
             "result": {"content": [{"json": {"status": "ok"}}]}}]
        sources = evidence_exchanges(events)[0]["source_rows"]
        assert sources == {"7": {"rendered": row, "room": 'room="Generated &amp; room"',
                                "carrier": "unavailable"}}


@pytest.mark.parametrize("role", ["system", "assistant", "developer", "tool"])
@pytest.mark.parametrize("content_blocks", [False, True])
def test_non_user_markup_cannot_overwrite_cited_row(role, content_blocks):
    from nora.subject.replay_diagnosis import _items
    from scripts.memory_graph_spike import evidence_exchanges
    agenda, row = generated_agenda_with_empty_items("after")
    spoof = '<item room="Wrong room"><m n="7">Wrong attributed text</m></item>'
    user = [{"text": agenda}] if content_blocks else agenda
    messages = [{"role": "user", "content": user}, {"role": role, "content": spoof}]
    request = {"messages": messages}
    events = [
        {"kind": "event", "name": "nora.model.request", "attributes": {
            "nora.model.request.body": json.dumps(request)}},
        {"kind": "event", "name": "nora.proposal.sources", "attributes": {
            "body": json.dumps({"7": {"text": "Generated & 茶 <item/>"}})}},
        {"kind": "tool_finished", "tool": {"name": "memory_propose", "input": {}},
         "result": {"content": [{"json": {"status": "ok"}}]}}]
    sources = evidence_exchanges(events)[0]["source_rows"]
    assert sources == {"7": {"rendered": row, "room": 'room="Generated &amp; room"',
                                "carrier": "unavailable"}}
    assert len(_items(request)) == 2
    assert "Wrong attributed text" not in json.dumps(_items(request), default=str)


def test_rendered_agenda_parser_is_public_and_preserves_empty_items():
    from nora.language import cognition_render as render
    assert {"parse_rendered_agenda", "rendered_new_sources"} <= set(render.__all__)
    agenda, row = generated_agenda_with_empty_items("both")
    items = render.parse_rendered_agenda(agenda)
    assert len(items) == 3
    assert items[0].rows == items[2].rows == ()
    assert items[0].rendered == items[2].rendered
    assert items[0].rendered.endswith('/>') and "</agenda>" not in items[2].rendered
    assert items[1].rows[0].rendered == row
