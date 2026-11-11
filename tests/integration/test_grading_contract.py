"""Generated policy bindings reach the real grader; no outbound model requests."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from openai import AsyncOpenAI
from strands_evals import Case

from evals import grading_contract as binding
from evals.rebuilt.grading import Judge, criteria_for
from nora.subject.eval_evidence import EvidenceError
from scripts import memory_graph_spike as spike


def write_contract(tmp_path, *, entrypoint="replay", cases=None, selection=None,
                   judges=("generated-a", "generated-b"), purpose="regression",
                   sample_sha256="a" * 64, change=None):
    """Generated reference bytes, selected independently of the subject instruction."""
    data = {
        "version": 1, "entrypoint": entrypoint, "purpose": purpose,
        "judges": list(judges), "selection": selection or {},
        "implementation": {name: hashlib.sha256((binding._ROOT / name).read_bytes()).hexdigest()
                           for name in binding.IMPLEMENTATION_FILES},
        "reference_doctrine": "FROZEN_REFERENCE" if entrypoint == "eval" else "",
        "instructions": ({"memory_support": "FROZEN_SUPPORT",
                          "memory_coverage": "FROZEN_COVERAGE"}
                         if entrypoint in {"replay", "rescore"} else {}),
        "cases": cases or {}, "sample_sha256": sample_sha256,
    }
    if change:
        change(data)
    path = tmp_path / "grading-reference.json"
    path.write_text(json.dumps(data))
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    return path, sha


def load_generated(tmp_path, **kwargs):
    path, sha = write_contract(tmp_path, **kwargs)
    return binding.load_contract(path, sha, entrypoint=kwargs.get("entrypoint", "replay"),
                                 judges=kwargs.get("judges", ("generated-a", "generated-b")),
                                 selection=kwargs.get("selection", {}))






def eval_contract_flags(tmp_path, *, names, only=(), sample=1, judges=(), judge_samples=3,
                        memory_index=False):
    from evals.scenarios import selected_cases
    cases = {f"{name}:{case.name}": {
        "input_sha256": binding.digest(binding.case_scenario(case)),
        "expected_output": "", "units": []}
        for name in names for case in selected_cases(
            name, only=frozenset(only) if only else None, memory_index=memory_index)}
    path, sha = write_contract(
        tmp_path, entrypoint="eval", judges=judges, cases=cases,
        selection={"corpora": names, "only": sorted(only), "sample": sample,
                   "judge_samples": judge_samples})
    return ["--grading-contract", str(path), "--grading-contract-sha256", sha]


@pytest.fixture
def generated_cli_contract(tmp_path, monkeypatch):
    """Unrelated CLI wiring tests get explicit generated references, not a bypass.

    These fixtures do not validate product labels. The binding tests below do
    not use this helper and exercise missing/wrong independently supplied files.
    """
    original = binding.contract_for
    def bind(args, *, entrypoint, judges, selection):
        import evals.__main__ as cli
        generated = tmp_path / "config.evaluation.toml"
        graph = args.memory_index or (
            generated.exists() and cli.memory_index_enabled(generated.read_text()))
        flags = eval_contract_flags(
            tmp_path, names=selection["corpora"], only=selection["only"],
            sample=selection["sample"], judge_samples=selection["judge_samples"],
            judges=judges, memory_index=graph)
        args.grading_contract, args.grading_contract_sha256 = Path(flags[1]), flags[3]
        return original(args, entrypoint=entrypoint, judges=judges, selection=selection)
    monkeypatch.setattr(binding, "contract_for", bind)


def memory_reference(inspection, units=True):
    return {"input_sha256": binding.digest(binding.memory_scenario(inspection)),
            "expected_output": "",
            "units": [{"id": "offer", "claim": "Offers a spare controller", "sources": [1]}]
            if units else []}


def completion(request, content):
    body = json.loads(request.content)
    assert request.url.host == "generated.invalid"
    return httpx.Response(200, json={
        "id": "generated-completion", "object": "chat.completion", "created": 1,
        "model": body["model"], "choices": [{"index": 0, "finish_reason": "stop",
                                              "message": {"role": "assistant",
                                                          "content": json.dumps(content)}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    })


@pytest.mark.parametrize("trace_available", [False, True])
@pytest.mark.parametrize("finish_reason", ["stop", "length"])
async def test_raw_reply_judge_captures_its_request_response_and_model_span(
        tmp_path, monkeypatch, trace_available, finish_reason):
    from tempfile import TemporaryFile

    from opentelemetry import trace

    from evals.backbone import _prefill_capture
    from nora.subject.eval_evidence import RunEvidence, read_events, validate_events

    requests = []

    def respond(request):
        body = json.loads(request.content)
        assert body["model"] == "generated-a" and body["max_tokens"] == 4096
        assert "Generated answer" in body["messages"][0]["content"]
        requests.append(body)
        response = completion(request, [{"criterion": "no_overreach_or_fabrication",
            "score": 1, "confidence": 1, "rationale": "Generated support"}])
        data = response.json()
        data["choices"][0]["finish_reason"] = finish_reason
        return httpx.Response(200, json=data)

    monkeypatch.setattr(Judge, "_endpoint", lambda self: (
        "https://generated.invalid/v1", "generated-key", self.model))
    monkeypatch.setattr(Judge, "_client", lambda self: AsyncOpenAI(
        base_url="https://generated.invalid/v1", api_key="generated-key", max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond))))
    if not trace_available:
        monkeypatch.setattr("nora.subject.raw_model.start_raw_model_span", lambda **kwargs: None)
    _prefill_capture()
    with TemporaryFile() as lock:
        evidence = RunEvidence(run_id="generated-raw-judge", metadata={},
            lock_fd=lock.fileno(), private_root=tmp_path / "private")
        with evidence.capture(trace.get_tracer_provider()):
            result = await Judge(base_url="https://generated.invalid/v1",
                model="generated-a", samples=1).grade(
                    said="Generated answer", asked="Generated question", doctrine="Generated rule")
        rows = read_events(evidence.path)
    if not trace_available:
        assert result.verdict == "REVIEW" and "trace unavailable" in result.error
        assert requests == []
        return
    assert result.verdict == ("PASS" if finish_reason == "stop" else "REVIEW")
    assert len(requests) == 1
    actual = [json.loads(e["attributes"]["nora.model.request.body"])
              for e in rows if e.get("name") == "nora.model.request"]
    assert actual == requests
    assert sum(e["kind"] == "transport_response" for e in rows) == 1
    models = [e for e in rows if e["kind"] == "span_finished"
              and e.get("attributes", {}).get("gen_ai.operation.name") == "chat"]
    assert len(models) == 1
    assert models[0]["attributes"]["gen_ai.usage.input_tokens"] == 10
    assert models[0]["attributes"]["gen_ai.usage.output_tokens"] == 5
    assert models[0]["attributes"]["gen_ai.response.model"] == "generated-a"
    root = next(e for e in rows if e["kind"] == "span_finished" and e["name"] == "eval.run")
    assert models[0]["trace_id"] == root["trace_id"]
    assert models[0]["parent_span_id"] == root["span_id"]
    assert validate_events(rows)["status"] == "PASS"
    if finish_reason == "length":
        subject_rows = [{**e, "model_role": "subject"}
                        if e["kind"] == "transport_response" else e for e in rows]
        assert "truncated" in validate_events(subject_rows)["causes"]
        unmarked_rows = [{k: v for k, v in e.items() if k != "model_role"} for e in rows]
        assert "truncated" in validate_events(unmarked_rows)["causes"]


def test_contract_drift_is_refused_and_valid_binding_is_immutable(tmp_path):
    path, sha = write_contract(tmp_path)
    good = binding.load_contract(path, sha, entrypoint="replay",
                                 judges=("generated-a", "generated-b"), selection={})
    decoded = good.data
    decoded["instructions"]["memory_support"] = "changed after binding"
    assert good.instruction("memory_support") == "FROZEN_SUPPORT"
    path.write_text(path.read_text() + " ")
    with pytest.raises(EvidenceError, match="fingerprint mismatch"):
        binding.load_contract(path, sha, entrypoint="replay",
                              judges=("generated-a", "generated-b"), selection={})
    with pytest.raises(EvidenceError, match="requires --grading-contract"):
        binding.load_contract(None, None, entrypoint="replay", judges=(), selection={})


@pytest.mark.parametrize("change, message", [
    (lambda d: d["implementation"].update({"evals/rebuilt/grading.py": "0" * 64}),
     "implementation differs"),
    (lambda d: d.update(judges=["different-judge"]), "selection or purpose"),
    (lambda d: d.update(selection={"sample": "changed"}), "selection or purpose"),
    (lambda d: d.update(purpose="calibration"), "selection or purpose"),
    (lambda d: d["instructions"].update(unused="ignored"), "unused or missing"),
])
def test_wrong_measurement_cannot_silently_run(tmp_path, change, message):
    with pytest.raises(EvidenceError, match=message):
        load_generated(tmp_path, change=change)


@pytest.mark.parametrize("changed", [
    "nora/subject/eval_evidence.py", "nora/agent/telemetry.py", "nora/obs/tracing.py",
    "nora/subject/raw_model.py", "nora/agent/replay_capture.py",
    "nora/language/cognition_render.py",
])
def test_capture_or_export_change_invalidates_the_fixed_measurement(tmp_path, monkeypatch, changed):
    path, sha = write_contract(tmp_path)
    original_root = binding._ROOT
    isolated = tmp_path / "implementation"
    for name in binding.IMPLEMENTATION_FILES:
        target = isolated / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(original_root / name)
    target = isolated / changed
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    target.write_bytes((original_root / changed).read_bytes())
    monkeypatch.setattr(binding, "_ROOT", isolated)
    binding.load_contract(path, sha, entrypoint="replay",
                          judges=("generated-a", "generated-b"), selection={})
    target.write_bytes(target.read_bytes() + b"\n# changed capture implementation\n")
    with pytest.raises(EvidenceError, match="implementation differs"):
        binding.load_contract(path, sha, entrypoint="replay",
                              judges=("generated-a", "generated-b"), selection={})


@pytest.mark.parametrize("metadata", [
    {"entry": "tag", "criteria": ["social_suitability"]},
    {"entry": "tag", "criteria": ["contextual_identity"]},
    {"entry": "tag", "criteria": ["lesson_applied"]},
    {"entry": "standing"},
])
def test_required_acceptance_cannot_bind_without_a_judge(tmp_path, metadata):
    case = Case(name="generated", input="Generated request", metadata=metadata)
    references = {"generated:generated": {
        "input_sha256": binding.digest(binding.case_scenario(case)),
        "expected_output": "A required semantic property", "units": []}}
    without_judge = load_generated(tmp_path, entrypoint="eval", judges=(), cases=references)
    with pytest.raises(EvidenceError, match="requires an active judge"):
        without_judge.verify_cases({"generated:generated": case})
    with_judge = load_generated(tmp_path, entrypoint="eval", judges=("generated-a",),
                                cases=references)
    with_judge.verify_cases({"generated:generated": case})


@pytest.mark.parametrize("entrypoint,purpose", [
    ("rescore", "confirmation"), ("diagnostic", "regression"),
])
def test_regrading_is_never_fresh_behavioral_confirmation(tmp_path, entrypoint, purpose):
    with pytest.raises(EvidenceError, match="selection or purpose"):
        load_generated(tmp_path, entrypoint=entrypoint, purpose=purpose)


async def test_actual_reply_judge_uses_fixed_reference_when_writer_changes(tmp_path, monkeypatch):
    case = Case(name="generated", input="A generated chat request", expected_output="old",
                metadata={"entry": "tag", "criteria": ["contextual_identity"]})
    contract = load_generated(tmp_path, entrypoint="eval", judges=("generated-a",), cases={
        "generated:generated": {"input_sha256": binding.digest(binding.case_scenario(case)),
                                "expected_output": "FROZEN_ACCEPTANCE", "units": []}})
    contract.verify_cases({"generated:generated": case})
    reloaded = case.model_copy(update={"session_id": "different-generated-execution"})
    contract.verify_cases({"generated:generated": reloaded})
    bound = contract.bind_case("generated", case)
    assert bound.input == case.input and bound.metadata == case.metadata
    requests = []

    def respond(request):
        body = json.loads(request.content)
        assert body["model"] == "generated-a"
        prompt = body["messages"][0]["content"]
        requests.append(prompt)
        correct = "FROZEN_REFERENCE" in prompt and "FROZEN_ACCEPTANCE" in prompt
        return completion(request, [
            {"criterion": name, "score": 1 if correct else 0, "confidence": 1,
             "rationale": "Checked against the independently selected reference"}
            for name in ("no_overreach_or_fabrication", "contextual_identity")])

    monkeypatch.setattr(Judge, "_endpoint", lambda _: (
        "https://generated.invalid/v1", "generated-key", "generated-a"))
    monkeypatch.setattr(Judge, "_client", lambda _: AsyncOpenAI(
        base_url="https://generated.invalid/v1", api_key="generated-key", max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond))))
    judge = Judge(base_url="https://generated.invalid/v1", model="generated-a",
                  samples=1, contract=contract)
    for candidate in ("CANDIDATE_POLICY_ONE", "CANDIDATE_POLICY_TWO"):
        result = await judge.grade(
            asked=bound.input, said="A generated reply", doctrine=candidate,
            extra=criteria_for(("contextual_identity",), acceptance=bound.expected_output))
        assert result.verdict == "PASS"
    assert len(requests) == 2 and requests[0] == requests[1]
    assert all("CANDIDATE_POLICY" not in request for request in requests)
    changed_case = case.model_copy(update={"input": "different scenario"})
    with pytest.raises(EvidenceError, match="scenario differs"):
        contract.bind_case("generated", changed_case)


async def test_actual_memory_judges_bind_frozen_units_and_keep_diagnostics_unscored(
        tmp_path, monkeypatch):
    from nora.agent import structured

    inspection = {"system": "CANDIDATE_WRITER", "new_sources": {
        "1": {"text": "I have a spare controller you can borrow"}}}
    contract = load_generated(tmp_path, cases={"wake": memory_reference(inspection)})
    statement = {"what": "Offers a spare controller", "cite": [{"source": 1}]}
    exchange = {"name": "memory_propose", "input": {"statements": [statement]},
                "source_rows": inspection["new_sources"],
                "result": {"statements": [{"index": 0, "status": "saved"}]}}
    requests = []

    def respond(request):
        body = json.loads(request.content)
        assert body["model"] in {"generated-a", "generated-b"} and not body["stream"]
        instruction = body["messages"][0]["content"]
        requests.append(body)
        if instruction == "FROZEN_SUPPORT" + spike._FIXED_SUPPORT_OUTPUT:
            return completion(request, {"verdict": "supported", "reason": "Cited offer",
                "eligibility": "eligible", "eligibility_reason": "New conveyed offer"})
        assert instruction == "FROZEN_COVERAGE" + spike._FIXED_COVERAGE_OUTPUT
        assert "required_units" in str(body["messages"]) and "offer" in str(body["messages"])
        return completion(request, {"coverage": [{"id": "offer", "verdict": "covered",
            "reason": "The saved statement preserves the offer",
            "covered_by": {"statements": [0], "reads": []}}],
            "diagnostic_misses": ["The background colour is not stored"]})

    monkeypatch.setattr(spike.Judges, "_endpoint", lambda _, model: (
        "https://generated.invalid/v1", "generated-key", model))
    original = structured.build_structured_model
    monkeypatch.setattr(spike, "build_structured_model", lambda **kwargs: original(
        **kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond))))
    from scripts import memory_graph_facts
    monkeypatch.setattr(
        memory_graph_facts, "_FACT_SUPPORT_INSTRUCTION", "CHANGED_CANDIDATE_SUPPORT")
    monkeypatch.setattr(
        memory_graph_facts, "_MISSED_FACT_INSTRUCTION", "CHANGED_CANDIDATE_COVERAGE")
    judges = spike.Judges(SimpleNamespace(llm=SimpleNamespace(timeout_seconds=5)),
                          ["generated-a", "generated-b"], Path("generated.toml"))
    result = await spike.smoke_fact_support(judges, inspection, [exchange],
                                           contract=contract, reference_key="wake")
    assert len(requests) == 4
    assert result["facts_supported"] == result["coverage_units"] == 1
    assert result["facts_missed"] == result["facts_judge_errors"] == 0
    assert result["grading_contract_sha256"] == contract.sha256
    assert spike.fact_quality(result) == {"status": "PASS"}


@pytest.mark.parametrize("row,error,missed", [
    ({"id": "unlisted", "verdict": "missed", "reason": "Extra detail"}, True, 0),
    ({"id": "offer", "verdict": "covered", "reason": "No evidence"}, True, 0),
    ({"id": "offer", "verdict": "covered", "reason": "Wrong row",
      "covered_by": {"statements": [], "reads": [0]}}, True, 0),
    ({"id": "offer", "verdict": "covered", "reason": "Successful shown read",
      "covered_by": {"statements": [], "reads": [1]}}, False, 0),
    ({"id": "offer", "verdict": "covered", "reason": "Extra proof key",
      "covered_by": {"statements": [], "reads": [1], "note": "Generated"}}, True, 0),
    ({"id": "offer", "verdict": "missed", "reason": "The offer is absent"}, False, 1),
    ({"id": "offer", "verdict": "unsure", "reason": "Cannot determine"}, False, 0),
])
async def test_fixed_coverage_is_total_and_requires_shown_evidence(tmp_path, row, error, missed):
    inspection = {"new_sources": {"1": {"text": "Generated offer"}}}
    contract = load_generated(tmp_path, cases={"wake": memory_reference(inspection)})
    async def call(model, instruction, payload, **kwargs):
        assert instruction == "FROZEN_COVERAGE" + spike._FIXED_COVERAGE_OUTPUT
        assert payload["required_units"][0]["id"] == "offer"
        return json.dumps({"coverage": [row], "diagnostic_misses": []})
    result = await spike.smoke_fact_support(
        SimpleNamespace(models=["generated-a", "generated-b"], call=call), inspection,
        [{"name": "memory_read", "input": {}, "result": {"status": "unavailable"}},
         {"name": "memory_read", "input": {},
          "result": {"status": "ok", "content": "Generated offer"}}],
        contract=contract, reference_key="wake")
    assert result["facts_judge_errors"] == (2 if error else 0)
    assert result["facts_missed"] == missed  # the same miss reported twice is one unit
    assert spike.fact_quality(result)["status"] == (
        "ERROR" if error or row["verdict"] == "unsure" else "FAIL" if missed else "PASS")


def test_exact_cohort_and_sources_are_bound(tmp_path):
    inspection = {"new_sources": {"1": {"text": "Generated offer"}}}
    contract = load_generated(tmp_path, cases={"1": memory_reference(inspection)})
    contract.verify_sample("a" * 64, [{"id": 1}])
    contract.memory_reference("1", inspection)
    with pytest.raises(EvidenceError, match="cohort differs"):
        contract.verify_sample("b" * 64, [{"id": 1}])
    with pytest.raises(EvidenceError, match="exact cohort"):
        contract.verify_sample("a" * 64, [{"id": 2}])
    with pytest.raises(EvidenceError, match="shown scenario differs"):
        contract.memory_reference("1", {"new_sources": {}})


def test_scenario_binding_matches_actual_transport_without_trusting_work_metadata(tmp_path):
    inspection = {"system": "Candidate policy", "agenda": "Generated — offer",
                  "new_sources": {"1": {"text": "Generated — offer"}}, "work": ["bookkeeping"]}
    contract = load_generated(tmp_path, cases={"1": memory_reference(inspection)})
    actual = {"system": "Different candidate policy", "agenda": "Generated, offer",
              "new_sources": {"1": {"text": "Generated, offer"}}}
    contract.memory_reference("1", actual)
    with pytest.raises(EvidenceError, match="shown scenario differs"):
        contract.memory_reference("1", {**actual, "agenda": "Different shown context"})








def test_acceptance_without_a_consumer_refuses_before_subject(tmp_path):
    case = Case(name="generated", input="Generated", metadata={"entry": "tag"})
    contract = load_generated(tmp_path, entrypoint="eval", cases={
        "generated:generated": {"input_sha256": binding.digest(binding.case_scenario(case)),
                                "expected_output": "An ignored obligation", "units": []}})
    with pytest.raises(EvidenceError, match="does not consume"):
        contract.verify_cases({"generated:generated": case})


@pytest.mark.parametrize("support,eligibility,quality", [
    ("supported", "ineligible", "FAIL"),
    ("unsupported", "eligible", "FAIL"),
    ("supported", "unsure", "ERROR"),
])
async def test_optional_claims_still_need_grounding_and_eligibility_is_separate(
        tmp_path, support, eligibility, quality):
    inspection = {"new_sources": {"1": {"text": "A generated image description"}}}
    contract = load_generated(tmp_path, cases={"wake": memory_reference(inspection, units=False)})
    statement = {"what": "An optional visual detail", "cite": [{"source": 1}]}
    async def call(model, instruction, payload, **kwargs):
        if "statement" in payload:
            assert instruction == "FROZEN_SUPPORT" + spike._FIXED_SUPPORT_OUTPUT
            assert payload["cited_rows"]
            return json.dumps({"verdict": support, "reason": "Source comparison",
                               "eligibility": eligibility, "eligibility_reason": "Scope check"})
        assert instruction == "FROZEN_COVERAGE" + spike._FIXED_COVERAGE_OUTPUT
        assert payload["required_units"] == []
        return '{"coverage":[],"diagnostic_misses":[]}'
    result = await spike.smoke_fact_support(
        SimpleNamespace(models=["generated-a", "generated-b"], call=call), inspection,
        [{"name": "memory_propose", "input": {"statements": [statement]},
          "source_rows": inspection["new_sources"],
          "result": {"statements": [{"index": 0, "status": "saved"}]}}],
        contract=contract, reference_key="wake")
    assert result["facts_supported"] == int(support == "supported")
    assert result["facts_unsupported"] == int(support == "unsupported")
    assert result["facts_missed"] == 0
    assert spike.fact_quality(result)["status"] == quality


def test_standard_cli_requires_contract_before_body(tmp_path, monkeypatch, capsys):
    import evals.__main__ as cli
    from nora.subject import sandbox
    monkeypatch.setattr(sandbox, "EVAL_HOST_LOCK", tmp_path / "eval.lock")
    def forbidden(*args, **kwargs):
        pytest.fail("missing contract reached execution")
    monkeypatch.setattr(cli, "_execute", forbidden)
    monkeypatch.setattr("sys.argv", ["evals", "tool_selection"])
    assert cli.main() == 2
    assert "requires --grading-contract" in capsys.readouterr().err


def test_standard_cli_does_not_ignore_contract_on_listing(tmp_path, monkeypatch):
    import evals.__main__ as cli
    from nora.subject import sandbox
    monkeypatch.setattr(sandbox, "EVAL_HOST_LOCK", tmp_path / "eval.lock")
    monkeypatch.setattr("sys.argv", ["evals", "--list", "--grading-contract", "unused.json"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2




@pytest.mark.parametrize("outside_proof", [False, True])
async def test_fixed_coverage_wire_labels_sparse_saved_and_read_indices(
        tmp_path, monkeypatch, outside_proof):
    from xml.etree import ElementTree

    from nora.agent import structured

    things = ("book", "cup", "headset", "map", "controller")
    inspection = {"new_sources": {
        str(i + 1): {"text": f"I offer a spare {thing}"}
        for i, thing in enumerate(things)}}
    reference = memory_reference(inspection)
    reference["units"][0]["sources"] = [5]
    contract = load_generated(tmp_path, cases={"wake": reference})
    statements = [{"what": f"Offers a spare {thing}", "cite": [{"source": i + 1}]}
                  for i, thing in enumerate(things)]
    exchanges = [
        {"name": "memory_read", "input": {}, "result": {"status": "unavailable"}},
        {"name": "memory_read", "input": {},
         "result": {"status": "ok", "content": "Generated shown record"}},
        {"name": "memory_propose", "input": {"statements": statements},
         "source_rows": inspection["new_sources"], "result": {"statements": [
             {"index": i, "status": "refused" if i == 1 else "saved"}
             for i in range(5)]}},
    ]
    original_exchanges = json.loads(json.dumps(exchanges))
    coverage_seen = []

    def respond(request):
        body = json.loads(request.content)
        content = body["messages"][1]["content"]
        if not isinstance(content, str):
            content = "".join(part["text"] for part in content)
        payload = json.loads("".join(
            ElementTree.fromstring(content).itertext()))  # noqa: S314 - own generated block
        if "statement" in payload:
            claim = payload["statement"]["what"]
            assert claim in {s["what"] for i, s in enumerate(statements) if i != 1}
            assert payload["cited_rows"]
            return completion(request, {
                "verdict": "supported", "reason": claim,
                "eligibility": "eligible", "eligibility_reason": "An offered object",
            })
        supplied = payload["saved_statements"]
        assert [s["evidence_index"] for s in supplied] == [0, 1, 2, 3]
        assert [{k: v for k, v in s.items() if k != "evidence_index"} for s in supplied] == [
            statements[i] for i in (0, 2, 3, 4)]
        assert [r["evidence_index"] for r in payload["session_input"]["reads"]] == [0, 1]
        assert "evidence_index" in body["messages"][0]["content"]
        coverage_seen.append(body["model"])
        controller = next(s for s in supplied if s["what"] == "Offers a spare controller")
        return completion(request, {"coverage": [{
            "id": "offer", "verdict": "covered", "reason": "Saved controller offer",
            "covered_by": {"statements": [4 if outside_proof else controller["evidence_index"]],
                           "reads": []}}], "diagnostic_misses": []})

    monkeypatch.setattr(spike.Judges, "_endpoint", lambda _, model: (
        "https://generated.invalid/v1", "generated-key", model))
    original = structured.build_structured_model
    monkeypatch.setattr(spike, "build_structured_model", lambda **kwargs: original(
        **kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond))))
    judges = spike.Judges(SimpleNamespace(llm=SimpleNamespace(timeout_seconds=5)),
                          ["generated-a", "generated-b"], Path("generated.toml"))
    result = await spike.smoke_fact_support(
        judges, inspection, exchanges, contract=contract, reference_key="wake")
    assert coverage_seen == ["generated-a", "generated-b"]
    assert result["facts_saved"] == result["facts_supported"] == 4
    assert result["facts_judge_errors"] == (2 if outside_proof else 0)
    assert spike.fact_quality(result) == {"status": "ERROR" if outside_proof else "PASS"}
    assert exchanges == original_exchanges


