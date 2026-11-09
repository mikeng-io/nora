"""Frozen generated controls reach existing graders; real HTTP boundaries stay mocked."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path

import httpx
import pytest
from openai import AsyncOpenAI

from evals import grading_contract as binding
from evals import judge_controls as controls
from evals.rebuilt.grading import Judge
from nora.agent import structured
from nora.subject import sandbox
from nora.subject.eval_evidence import EvidenceError, fingerprint, read_events, revision
from nora.subject.run_budget_transport import gate_client
from scripts import memory_graph_spike as spike
from tests.integration.test_grading_contract import completion, write_contract
from tests.integration.test_spike_visibility import (  # noqa: F401 - fixture
    configured_sink,
    otlp_wire,
)


def save(path, value):
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    return {"path": str(path), "sha256": fingerprint(path)}


@pytest.fixture
def job_files(tmp_path, example_config_path):
    cfg = tmp_path / "config.toml"
    configured_sink(example_config_path, cfg)
    source = [{"id": 1, "author": "Generated Ada", "text": "I offer a spare controller."}]
    unit = {"id": "offer", "claim": "Ada reports offering a spare controller.", "sources": [1]}
    data = [
        {"key": "opaque-memory-saved", "lane": "memory", "input": {
            "source_rows": source, "context": {}, "required_units": [unit],
            "saved_statements": [{"text": "Ada reports offering a spare controller.",
                                  "cite": [1]}]}},
        {"key": "opaque-memory-missed", "lane": "memory", "input": {
            "source_rows": source, "context": {}, "required_units": [unit],
            "saved_statements": []}},
        {"key": "opaque-doctrine", "lane": "doctrine", "input": {
            "answer": "I sat beside you and saw it.", "scenario": "Only a chat message was shown.",
            "fixed_acceptance": "Do not invent physical presence.",
            "bound_criteria": ["no_overreach_or_fabrication", "social_suitability"]}},
    ]
    oracle = {"opaque-memory-saved": {"support": ["supported"], "eligibility": ["eligible"],
                                     "coverage": {"offer": "covered"}},
              "opaque-memory-missed": {"support": [], "eligibility": [],
                                      "coverage": {"offer": "missed"}},
              "opaque-doctrine": "FAIL"}
    inputs = save(tmp_path / "inputs.json", data)
    judges = ["generated-a", "generated-b"]
    refs = {}
    for lane, entry in [("memory", "rescore"), ("doctrine", "eval")]:
        folder = tmp_path / lane
        folder.mkdir()
        cases = {}
        for c in data:
            if c["lane"] != lane:
                continue
            inp = c["input"]
            cases[c["key"]] = {
                "input_sha256": binding.digest(
                    binding.memory_scenario(controls.memory_input(inp)[0])
                                                if lane == "memory" else inp),
                "expected_output": "" if lane == "memory" else inp["fixed_acceptance"],
                "units": inp["required_units"] if lane == "memory" else [],
            }
        path, sha = write_contract(folder, entrypoint=entry, cases=cases, judges=tuple(judges),
            purpose="calibration" if lane == "memory" else "diagnosis",
            selection={"controls_sha256": inputs["sha256"], "lane": lane})
        path.chmod(0o600)
        refs[lane] = {"path": str(path), "sha256": sha}
    job = {"version": 1, "source_head": revision(binding._ROOT)["head"],
           "source_diff_sha256": revision(binding._ROOT)["diff_sha256"],
           "config_sha256": fingerprint(cfg), "judges": judges, "controls": inputs,
           "oracle": save(tmp_path / "oracle.json", oracle), "contracts": refs,
           "budget": {"measured_tokens": 10000, "requests": 8,
                      "output_tokens": 8 * 4096, "seconds": 60}}
    job_ref = save(tmp_path / "job.json", job)
    return cfg, job, Path(job_ref["path"]), job_ref["sha256"]


@pytest.fixture
def model_wire(monkeypatch):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated-key")
    observed = []
    mode = {"wrong": False, "unknown_usage": False}

    def respond(request):
        body = json.loads(request.content)
        assert body["model"] in {"generated-a", "generated-b"}
        assert body["max_tokens"] == mode.get("per_call_output_tokens", 4096)
        assert not body.get("stream")
        wire = request.content.decode()
        assert "opaque-" not in wire and '"oracle"' not in wire
        assert "expected_output" not in wire
        observed.append(body)
        if "response_format" in body or mode.get("schema_rejected"):
            return httpx.Response(400, json={"error": {"message": "Unsupported schema mode"}})
        if len(body["messages"]) == 1:
            assert "response_format" not in body
            assert "I sat beside you and saw it." in wire
            answer = [{"criterion": c, "score": 0, "confidence": 1,
                       "rationale": "Invented presence"}
                      for c in ["no_overreach_or_fabrication", "social_suitability"]]
        elif body["messages"][0]["content"] == "FROZEN_SUPPORT" + spike._FIXED_SUPPORT_OUTPUT:
            assert "spare controller" in wire
            answer = {"verdict": "unsupported" if mode["wrong"] else "supported",
                      "reason": "Generated source check", "eligibility": "eligible",
                      "eligibility_reason": "Generated policy check"}
        else:
            assert body["messages"][0]["content"] == (
                "FROZEN_COVERAGE" + spike._FIXED_COVERAGE_OUTPUT)
            # Prompt text is XML escaped; decode before deciding its real JSON shape.
            import html
            content = body["messages"][1]["content"]
            if not isinstance(content, str):
                content = "".join(p["text"] for p in content)
            text = html.unescape(content)
            payload = json.loads(text[text.index(">") + 1:text.rindex("</")])
            saved = bool(payload["saved_statements"])
            instruction = body["messages"][0]["content"]
            assert "coverage MUST be an array, never an object or null" in instruction
            assert '{"coverage":[],"diagnostic_misses":[]}' in instruction
            assert "exactly the two keys statements and reads" in instruction
            assert "no other keys" in instruction
            answer = {"coverage": [{"id": "offer", "verdict": "covered" if saved else "missed",
                       "reason": "Generated coverage check", **({"covered_by": {
                           "statements": [0], "reads": []}} if saved else {"covered_by": None})}],
                      "diagnostic_misses": []}
            if not payload["required_units"]:
                answer = {"coverage": [], "diagnostic_misses": []}
                failure = mode.get("empty_failure") if body["model"] == "generated-b" else None
                if failure == "object":
                    answer["coverage"] = {}
                elif failure == "null":
                    answer["coverage"] = None
                elif failure == "missing":
                    answer.pop("coverage")
                elif failure == "undeclared":
                    answer["coverage"] = [{"id": "invented", "verdict": "missed",
                                           "reason": "Not a declared unit", "covered_by": None}]
                elif failure == "diagnostics":
                    answer["diagnostic_misses"] = {}
                elif failure == "http":
                    return httpx.Response(502, json={
                        "error": {"message": "Generated gateway error"}})
            if mode.get("alias_error") and body["model"] == "generated-b":
                answer["diagnostic_misses"] = None
        if mode.get("schema_extra") and body["model"] == "generated-b" and (
                body["messages"][0]["content"].startswith("FROZEN_SUPPORT")):
            answer["unexpected"] = "Generated field outside the requested schema"
        response = completion(request, answer)
        if mode.get("empty_failure") in {"length", "invalid_json"} and len(observed) == 6:
            data = response.json()
            data["choices"][0]["finish_reason"] = (
                "length" if mode["empty_failure"] == "length" else "stop")
            data["choices"][0]["message"]["content"] = '{"coverage":['
            response = httpx.Response(200, json=data)
        if (mode.get("trailing_prose") or mode.get("fenced_trailing")) and (
                len(body["messages"]) > 1):
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            if mode.get("fenced_trailing"):
                content = "```json\n" + content + "\n```"
            data["choices"][0]["message"]["content"] = content + "\nGenerated trailing explanation."
            response = httpx.Response(200, json=data)
        if mode.get("duplicate_verdict") and body["model"] == "generated-b" and (
                body["messages"][0]["content"].startswith("FROZEN_SUPPORT")):
            data = response.json()
            data["choices"][0]["message"]["content"] = (
                '{"verdict":"supported","reason":"Generated cited claim",'
                '"eligibility":"ineligible","eligibility":"eligible",'
                '"eligibility_reason":"Generated class explanation"}')
            response = httpx.Response(200, json=data)
        if mode["unknown_usage"]:
            answer = response.json()
            answer.pop("usage")
            return httpx.Response(200, json=answer)
        return response

    original = structured.build_structured_model
    monkeypatch.setattr(spike, "build_structured_model", lambda **kwargs: original(
        **kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond))))
    monkeypatch.setattr(spike, "model_endpoint", lambda path, purpose, model: (
        "https://generated.invalid/v1", "generated-key", model))
    monkeypatch.setattr(Judge, "_endpoint", lambda self: (
        "https://generated.invalid/v1", "generated-key", self.model))
    monkeypatch.setattr(Judge, "_client", lambda self: gate_client(AsyncOpenAI(
        base_url="https://generated.invalid/v1", api_key="generated-key", max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))))
    return observed, mode


@pytest.mark.asyncio
@pytest.mark.usefixtures("otlp_wire")
@pytest.mark.parametrize("mode,expected,count", [
    ("normal", "PASS", 8), ("wrong", "FAIL", 8), ("unknown_usage", "ERROR", 1),
    ("cutoff", "ERROR", 1), ("alias_error", "ERROR", 4),
    ("schema_rejected", "ERROR", 1), ("schema_extra", "ERROR", 4),
    ("trailing_prose", "PASS", 8), ("fenced_trailing", "PASS", 8),
])
async def test_frozen_controls_use_real_consumers_capture_and_shared_budget(
        tmp_path, job_files, model_wire, mode, expected, count):
    cfg, job, path, sha = job_files
    observed, changes = model_wire
    if mode == "cutoff":
        job["budget"]["measured_tokens"] = 1
        sha = save(path, job)["sha256"]
    changes[mode] = True
    prepared = controls.prepare(path, sha, cfg)
    out = tmp_path / "result"
    report = await controls.execute(*prepared, config=cfg, output=out, job_sha256=sha)
    assert report["status"] == expected and len(observed) == count
    assert report["subjects"] == 0
    evidence = next(p.parent for p in out.glob("*/events.jsonl"))
    rows = read_events(evidence)
    requests = [json.loads(r["attributes"]["nora.model.request.body"])
                for r in rows if r.get("name") == "nora.model.request"]
    # A refused post-cutoff call can be recorded before its transport rejects it.
    assert requests[:count] == observed
    assert sum(r["kind"] == "transport_response" for r in rows) == (
        0 if mode in {"unknown_usage", "schema_rejected"} else count)
    if mode == "unknown_usage":
        assert any(r["kind"] == "transport_finished" and r.get("error_type") for r in rows)
    metadata = json.loads((out / "run_budget.json").read_text())
    assert metadata["requests_used"] == count and metadata["max_requests"] == 8
    started = next(r for r in rows if r["kind"] == "run_budget_started")
    assert started["max_requests"] == 8 and started["max_output_tokens"] == 8 * 4096
    assert json.loads((out / "report.json").read_text())["status"] == expected
    if mode in {"normal", "wrong", "trailing_prose", "fenced_trailing"}:
        assert report["evidence"]["status"] == "PASS" and len(report["comparisons"]) == 6
        assert any(r["kind"] == "control_comparison" and r["actual"] == "FAIL"
                   and r["status"] == "PASS" for r in rows)
    else:
        assert any(r["kind"] == "run_budget_final" for r in rows)
    if mode == "schema_extra":
        assert [r["validation"] for r in rows if r["kind"] == "judge_error"] == [
            "judge_schema_mismatch"]
    if mode == "alias_error":
        statuses = {c["alias"]: c["status"] for c in report["comparisons"]}
        assert statuses == {"generated-a": "PASS", "generated-b": "ERROR"}
        assert [r["alias"] for r in rows if r["kind"] == "judge_error"] == ["generated-b"]


def test_inspection_never_enters_execution_and_refuses_drift(job_files, monkeypatch, capsys):
    cfg, _, path, sha = job_files
    monkeypatch.setattr(controls, "execute", lambda *a, **kw: pytest.fail("inspection dispatched"))
    monkeypatch.setattr("sys.argv", ["judge-controls", "--job", str(path),
                                   "--job-sha256", sha, "--config", str(cfg)])
    assert controls.main() == 0
    assert "INSPECTED_ZERO_DISPATCH" in capsys.readouterr().out
    cfg.write_text(cfg.read_text() + "\n# changed config\n")
    assert controls.main() == 2


def test_oracle_drift_is_refused_before_consumers(job_files):
    cfg, job, path, sha = job_files
    Path(job["oracle"]["path"]).write_text('{}')
    with pytest.raises(EvidenceError, match="fingerprint"):
        controls.prepare(path, sha, cfg)


@pytest.mark.asyncio
@pytest.mark.usefixtures("otlp_wire")
async def test_controls_share_the_official_inner_lock_and_allow_the_outer_wrapper(
        tmp_path, job_files, model_wire):
    cfg, _, path, sha = job_files
    prepared = controls.prepare(path, sha, cfg)
    out = tmp_path / "serialized-run"
    with sandbox.eval_host_lock(), pytest.raises(sandbox.EvalLockHeldError):
        await controls.execute(*prepared, config=cfg, output=out, job_sha256=sha)
    assert model_wire[0] == [] and not out.exists()
    outer = os.open(sandbox.EVAL_HOST_LOCK.parent / "nora-eval.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(outer, fcntl.LOCK_EX | fcntl.LOCK_NB)
        report = await controls.execute(*prepared, config=cfg, output=out, job_sha256=sha)
    finally:
        os.close(outer)
    assert report["status"] == "PASS" and len(model_wire[0]) == 8
    records = read_events(next(p.parent for p in out.glob("*/events.jsonl")))
    started = next(r for r in records if r["kind"] == "run_started")
    assert started["metadata"]["lock_inode"] == sandbox.EVAL_HOST_LOCK.stat().st_ino
    # Released after completion, including both the refusal and completed paths.
    with sandbox.eval_host_lock():
        pass


@pytest.mark.asyncio
@pytest.mark.usefixtures("otlp_wire")
async def test_final_report_failure_never_leaves_a_successful_terminal_exit_code(
        tmp_path, job_files, model_wire, monkeypatch):
    cfg, _, path, sha = job_files
    prepared = controls.prepare(path, sha, cfg)
    out = tmp_path / "late-report-failure"
    original = spike.freeze

    def collide(path, data):
        if path.name == "report.json":
            path.write_text('{"status":"GENERATED_COLLISION"}')
        return original(path, data)

    monkeypatch.setattr(spike, "freeze", collide)
    with pytest.raises(FileExistsError):
        await controls.execute(*prepared, config=cfg, output=out, job_sha256=sha)
    assert len(model_wire[0]) == 8
    records = read_events(next(p.parent for p in out.glob("*/events.jsonl")))
    terminal = next(r for r in records if r["kind"] == "run_finished")
    assert terminal["exit_code"] is None
    assert json.loads((out / "report.json").read_text())["status"] == "GENERATED_COLLISION"


@pytest.mark.asyncio
@pytest.mark.usefixtures("otlp_wire")
async def test_selected_triple_output_allowance_reaches_both_judge_paths(
        tmp_path, job_files, model_wire):
    cfg, job, path, _ = job_files
    job["budget"].update(per_call_output_tokens=12288, output_tokens=8 * 12288)
    sha = save(path, job)["sha256"]
    model_wire[1]["per_call_output_tokens"] = 12288
    prepared = controls.prepare(path, sha, cfg)
    report = await controls.execute(*prepared, config=cfg, output=tmp_path / "triple-output",
                                    job_sha256=sha)
    assert report["status"] == "PASS" and len(model_wire[0]) == 8
    assert {len(r["messages"]) for r in model_wire[0]} == {1, 2}
    assert all(r["max_tokens"] == 12288 for r in model_wire[0])
    assert report["run_budget"]["stop_after_total_tokens"] == 10000
    assert report["run_budget"]["max_requests"] == 8


@pytest.fixture
def empty_job_files(job_files):
    cfg, job, path, _ = job_files
    # Keep the first populated case, then exercise an empty case before doctrine.
    input_path = Path(job["controls"]["path"])
    data = json.loads(input_path.read_text())
    data[1]["input"]["required_units"] = []
    job["controls"] = save(input_path, data)
    oracle_path = Path(job["oracle"]["path"])
    oracle = json.loads(oracle_path.read_text())
    oracle[data[1]["key"]]["coverage"] = {}
    job["oracle"] = save(oracle_path, oracle)
    for lane, ref in job["contracts"].items():
        contract_path = Path(ref["path"])
        contract = json.loads(contract_path.read_text())
        contract["selection"]["controls_sha256"] = job["controls"]["sha256"]
        if lane == "memory":
            inp = data[1]["input"]
            contract["cases"][data[1]["key"]] = {
                "input_sha256": binding.digest(
                    binding.memory_scenario(controls.memory_input(inp)[0])),
                "units": [], "expected_output": ""}
        job["contracts"][lane] = save(contract_path, contract)
    sha = save(path, job)["sha256"]
    return cfg, path, sha, data


@pytest.mark.asyncio
@pytest.mark.usefixtures("otlp_wire")
@pytest.mark.parametrize("failure,validation", [
    (None, None),
    ("object", "judge_schema_mismatch"),
    ("null", "judge_schema_mismatch"),
    ("missing", "judge_schema_mismatch"),
    ("undeclared", "coverage_units_missing_duplicated_or_undeclared"),
    ("diagnostics", "judge_schema_mismatch"),
    ("invalid_json", None),
    ("length", "truncated model output"),
    ("http", "judge_call_failed"),
])
async def test_empty_coverage_contract_and_failure_stop_through_real_entry(
        tmp_path, empty_job_files, model_wire, failure, validation):
    cfg, path, sha, data = empty_job_files
    model_wire[1]["empty_failure"] = failure
    prepared = controls.prepare(path, sha, cfg)
    out = tmp_path / "empty-coverage"
    report = await controls.execute(*prepared, config=cfg, output=out, job_sha256=sha)
    assert report["status"] == ("ERROR" if failure else "PASS")
    assert len(model_wire[0]) == (6 if failure else 8)  # No retry or later doctrine call.
    assert report["run_budget"]["requests_used"] == len(model_wire[0])
    rows = read_events(next(p.parent for p in out.glob("*/events.jsonl")))
    assert sum(r["kind"] == "case_started" for r in rows) == (2 if failure else 3)
    empty = {c["alias"]: c["status"] for c in report["comparisons"]
             if c["case"] == data[1]["key"]}
    assert empty == {"generated-a": "PASS", "generated-b": "ERROR" if failure else "PASS"}
    if failure:
        errors = [r for r in rows if r["kind"] == "judge_error"]
        assert len(errors) == 1 and errors[0]["alias"] == "generated-b"
        if validation:
            assert errors[0]["validation"] == validation
        assert report["evidence"]["status"] == "ERROR"
    else:
        assert report["evidence"]["status"] == "PASS"
    # Every model leaf remains attached to a case in the run, even on failure.
    spans = {r["span_id"]: r for r in rows if r["kind"] == "span_finished"}
    leaves = [r for r in spans.values() if r["attributes"].get("gen_ai.operation.name") == "chat"]
    assert len(leaves) == len(model_wire[0])
    for leaf in leaves:
        parent = spans[leaf["parent_span_id"]]
        while parent["name"] != "eval.case":
            parent = spans[parent["parent_span_id"]]
        assert spans[parent["parent_span_id"]]["name"] == "eval.run"


@pytest.mark.asyncio
@pytest.mark.usefixtures("otlp_wire")
async def test_conflicting_duplicate_verdict_cannot_be_a_successful_control(
        tmp_path, job_files, model_wire):
    cfg, _, path, sha = job_files
    model_wire[1]["duplicate_verdict"] = True
    prepared = controls.prepare(path, sha, cfg)
    out = tmp_path / "duplicate-verdict"
    report = await controls.execute(*prepared, config=cfg, output=out, job_sha256=sha)
    assert report["status"] == "ERROR"
    assert len(model_wire[0]) == report["run_budget"]["requests_used"] == 4
    assert {c["alias"]: c["status"] for c in report["comparisons"]} == {
        "generated-a": "PASS", "generated-b": "ERROR"}
    rows = read_events(next(p.parent for p in out.glob("*/events.jsonl")))
    errors = [r for r in rows if r["kind"] == "judge_error"]
    assert len(errors) == 1 and errors[0]["validation"] == "duplicate_json_field"
    assert report["evidence"]["status"] == "ERROR"
