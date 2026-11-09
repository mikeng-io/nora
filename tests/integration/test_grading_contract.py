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


def attach_replay_contract(args, tmp_path, inspection=None, *, wakes=()):
    """An explicit synthetic run contract for existing real-entry checks."""
    args.judge_a = getattr(args, "judge_a", "generated-a")
    args.judge_b = getattr(args, "judge_b", "generated-b")
    selection = {k: getattr(args, k, None) for k in
                 ("seed", "per_stratum", "minimum", "smoke", "session_count")}
    sample_bytes = (json.dumps(list(wakes), sort_keys=True, ensure_ascii=False) + "\n").encode()
    args.grading_contract, args.grading_contract_sha256 = write_contract(
        tmp_path, judges=(args.judge_a, args.judge_b), selection=selection,
        sample_sha256=hashlib.sha256(sample_bytes).hexdigest(), cases={
            str(w["id"]): memory_reference(inspection, units=False) for w in wakes})


def attach_archive_contract(args, tmp_path, entrypoint, *, cases=None):
    source = args.rescore if entrypoint == "rescore" else args.decision_judge
    source.mkdir(exist_ok=True)
    if not list(source.glob("*/events.jsonl")) and not (source / "events.jsonl").exists():
        (source / "events.jsonl").write_text("")
    args.grading_contract, args.grading_contract_sha256 = write_contract(
        tmp_path, entrypoint=entrypoint, purpose="diagnosis", cases=cases,
        judges=(args.judge_a, args.judge_b),
        selection={"source_sha256": spike.fingerprint(spike.evidence_file(source))})


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
        if instruction == "FROZEN_SUPPORT":
            return completion(request, {"verdict": "supported", "reason": "Cited offer",
                "eligibility": "eligible", "eligibility_reason": "New conveyed offer"})
        assert instruction == "FROZEN_COVERAGE"
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
    monkeypatch.setattr(spike, "_FACT_SUPPORT_INSTRUCTION", "CHANGED_CANDIDATE_SUPPORT")
    monkeypatch.setattr(spike, "_MISSED_FACT_INSTRUCTION", "CHANGED_CANDIDATE_COVERAGE")
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
    ({"id": "offer", "verdict": "missed", "reason": "The offer is absent"}, False, 1),
    ({"id": "offer", "verdict": "unsure", "reason": "Cannot determine"}, False, 0),
])
async def test_fixed_coverage_is_total_and_requires_shown_evidence(tmp_path, row, error, missed):
    inspection = {"new_sources": {"1": {"text": "Generated offer"}}}
    contract = load_generated(tmp_path, cases={"wake": memory_reference(inspection)})
    async def call(model, instruction, payload):
        assert instruction == "FROZEN_COVERAGE" and payload["required_units"][0]["id"] == "offer"
        return json.dumps({"coverage": [row], "diagnostic_misses": []})
    result = await spike.smoke_fact_support(
        SimpleNamespace(models=["generated-a", "generated-b"], call=call), inspection,
        [{"name": "memory_read", "input": {}, "result": {"status": "unavailable"}}],
        contract=contract, reference_key="wake")
    assert result["facts_judge_errors"] == (2 if error else 0)
    assert result["facts_missed"] == missed  # the same miss reported twice is one unit
    assert spike.fact_quality(result)["status"] == (
        "ERROR" if error or row["verdict"] == "unsure" else "FAIL")


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


@pytest.mark.parametrize("drift", [False, True])
async def test_real_replay_preflight_refuses_cohort_drift_before_writer(
        tmp_path, monkeypatch, drift):
    from tests.unit.test_memory_graph_spike import generated_replay_environment
    env = generated_replay_environment(tmp_path, monkeypatch)
    monkeypatch.setattr(spike, "evaluation_export_for", lambda _: None)
    env.args.config.write_text("# generated configuration\n")
    attach_replay_contract(env.args, tmp_path, env.runner.inspect.return_value, wakes=[
        {"id": 1, "at_ms": 1, "strata": ["text"]},
        {"id": 2, "at_ms": 2, "strata": ["attachment"]}])
    if drift:
        path = env.args.grading_contract
        data = json.loads(path.read_text())
        data["sample_sha256"] = "b" * 64
        path.write_text(json.dumps(data))
        env.args.grading_contract_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        with pytest.raises(EvidenceError, match="cohort differs"):
            await spike.run(env.args)
        env.runner.replay.assert_not_awaited()
        env.runner.inspect.assert_not_awaited()
    else:
        report = await spike.run(env.args)
        assert env.runner.replay.await_count == 2
        assert report["grading_contract"]["sha256"] == env.args.grading_contract_sha256


@pytest.mark.parametrize("entrypoint", ["run", "run_rescore", "run_diagnostic"])
async def test_paid_spike_entrypoints_require_contract_before_export(
        tmp_path, monkeypatch, entrypoint):
    def forbidden(_):
        pytest.fail("missing contract reached exporter setup")
    monkeypatch.setattr(spike, "evaluation_export_for", forbidden)
    with pytest.raises(EvidenceError, match="requires --grading-contract"):
        await getattr(spike, entrypoint)(SimpleNamespace(config=tmp_path / "unused.toml"))


def test_inspection_does_not_require_or_silently_ignore_a_contract(tmp_path):
    args = SimpleNamespace(inspect_only=True)
    assert spike.spike_contract(args, "replay") is None
    args.grading_contract = tmp_path / "not-a-contract"
    with pytest.raises(EvidenceError, match="requires --grading-contract"):
        spike.spike_contract(args, "replay")
    with pytest.raises(EvidenceError, match="does not consume"):
        spike.spike_contract(SimpleNamespace(project_only=True,
                                             grading_contract=tmp_path / "unused"), "replay")


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
    async def call(model, instruction, payload):
        if "statement" in payload:
            assert instruction == "FROZEN_SUPPORT" and payload["cited_rows"]
            return json.dumps({"verdict": support, "reason": "Source comparison",
                               "eligibility": eligibility, "eligibility_reason": "Scope check"})
        assert instruction == "FROZEN_COVERAGE" and payload["required_units"] == []
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
