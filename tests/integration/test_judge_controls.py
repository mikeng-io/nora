"""Frozen generated controls reach existing graders; real HTTP boundaries stay mocked."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from openai import AsyncOpenAI

from evals import grading_contract as binding
from evals import judge_controls as controls
from evals.rebuilt.grading import Judge
from nora.agent import structured
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
        assert body["max_tokens"] == 4096 and not body.get("stream")
        wire = request.content.decode()
        assert "opaque-" not in wire and '"oracle"' not in wire
        assert "expected_output" not in wire
        observed.append(body)
        if len(body["messages"]) == 1:
            assert "I sat beside you and saw it." in wire
            answer = [{"criterion": c, "score": 0, "confidence": 1,
                       "rationale": "Invented presence"}
                      for c in ["no_overreach_or_fabrication", "social_suitability"]]
        elif body["messages"][0]["content"] == "FROZEN_SUPPORT":
            assert "spare controller" in wire
            answer = {"verdict": "unsupported" if mode["wrong"] else "supported",
                      "reason": "Generated source check", "eligibility": "eligible",
                      "eligibility_reason": "Generated policy check"}
        else:
            assert body["messages"][0]["content"] == "FROZEN_COVERAGE"
            # Prompt text is XML escaped; decode before deciding its real JSON shape.
            import html
            content = body["messages"][1]["content"]
            if not isinstance(content, str):
                content = "".join(p["text"] for p in content)
            text = html.unescape(content)
            payload = json.loads(text[text.index(">") + 1:text.rindex("</")])
            saved = bool(payload["saved_statements"])
            answer = {"coverage": [{"id": "offer", "verdict": "covered" if saved else "missed",
                       "reason": "Generated coverage check", **({"covered_by": {
                           "statements": [0], "reads": []}} if saved else {})}],
                      "diagnostic_misses": []}
        response = completion(request, answer)
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
    ("cutoff", "ERROR", 1),
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
        0 if mode == "unknown_usage" else count)
    if mode == "unknown_usage":
        assert any(r["kind"] == "transport_finished" and r.get("error_type") for r in rows)
    metadata = json.loads((out / "run_budget.json").read_text())
    assert metadata["requests_used"] == count and metadata["max_requests"] == 8
    started = next(r for r in rows if r["kind"] == "run_budget_started")
    assert started["max_requests"] == 8 and started["max_output_tokens"] == 8 * 4096
    assert json.loads((out / "report.json").read_text())["status"] == expected
    if mode in {"normal", "wrong"}:
        assert report["evidence"]["status"] == "PASS" and len(report["comparisons"]) == 6
        assert any(r["kind"] == "control_comparison" and r["actual"] == "FAIL"
                   and r["status"] == "PASS" for r in rows)
    else:
        assert any(r["kind"] == "run_budget_final" for r in rows)


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
