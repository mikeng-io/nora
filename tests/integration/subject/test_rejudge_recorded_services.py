"""Frozen rejudge uses real judge calls, private capture and owned HTTP recordings."""

from __future__ import annotations

import asyncio
import hashlib
import json
import socket
from dataclasses import replace
from pathlib import Path

import pytest
from aiohttp import web

from nora.subject.environment import IsolatedServices
from nora.subject.eval_evidence import read_events
from scripts.cognition_loop import rejudge as module
from scripts.cognition_loop.accounting import manifest
from scripts.cognition_loop.cases import Expectation, generated_case, write_case
from tests.integration.subject.evidence_fixtures import judge_trajectory

MODELS = ("generated-a", "generated-b")


@pytest.fixture
def frozen_run(tmp_path, example_config_path, monkeypatch):
    from nora.subject import sandbox

    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated-only")
    monkeypatch.setattr(sandbox, "EVAL_HOST_LOCK", tmp_path / "eval.lock")
    case = replace(generated_case(), expectations=tuple(
        Expectation(wake=i, polarity="must", kind="note", text="The generated note is retained.")
        for i in range(2)))
    write_case(case, tmp_path / "case.json")
    report = {"status": "PASS", "expected_cases": manifest([case]), "evidence": {
        "status": "PASS", "source_only": "generated original receipt"},
              "run_budget": {"generated_source": True},
              "cases": [{
            "case": case.name, "unit": case.unit, "status": "PASS", "wakes": [
                {"recorded_wake_id": ident, "status": "PASS", "at_ms": 1000 + i}
                for i, ident in enumerate(case.wakes)]}]}
    (tmp_path / "report.json").write_text(json.dumps(report))
    (tmp_path / "run_budget.json").write_text(json.dumps(report["run_budget"]))
    for i, ident in enumerate(case.wakes):
        (tmp_path / f"wake-{i}-{ident}.json").write_text(json.dumps({
            "diff": {"note": {"created": [{"status": "created", "entry_id": "n1", "after": {
                "text": f"Generated note {i}"}}]}},
            "recorded_wake_id": ident, "unit": case.unit, "at_ms": 1000 + i,
            "trajectory": judge_trajectory(unit_rooms=[["signal", "generated-room"]], blocks={
                "agenda": f"Generated source {i}"})}))
    cfg = tmp_path / "config.toml"
    cfg.write_bytes(example_config_path.read_bytes())
    loaded = module.load(cfg, require_secrets=False)
    bindings = {"llm.base_url": "judge"}
    bindings.update({f"providers.{name}.base_url": "judge" for name in loaded.providers})
    rows = []
    for i in range(2):
        for alias in MODELS:
            rows.append({"method": "POST", "path": "/v1/chat/completions", "response": {
                "status": 200, "headers": {"x-omniroute-model": alias + "-served"}, "json": {
                    "id": f"generated-{i}-{alias}", "object": "chat.completion", "created": 1,
                    "model": alias, "choices": [{"index": 0, "finish_reason": "stop", "message": {
                        "role": "assistant", "content": json.dumps({"verdict": "met",
                            "reason": f"Generated protocol response {i}",
                            "entry_ids": ["note:n1"]})}}],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}}}})
    services = {"version": 1, "corpora": {"cognition-rejudge": {case.name: {
        "version": 1, "bindings": bindings, "services": {"judge": rows}}}}}
    return case, cfg, services


def input_file(tmp_path, data):
    path = tmp_path / "recordings.json"
    path.write_text(json.dumps(data, indent=2))
    return path


@pytest.mark.parametrize("damage", [None, "route", "unused", "same_served", "citation"])
async def test_rejudge_captures_both_aliases_and_preserves_source(
    tmp_path, frozen_run, monkeypatch, damage,
):
    case, cfg, data = frozen_run
    rows = data["corpora"]["cognition-rejudge"][case.name]["services"]["judge"]
    if damage == "route":
        rows[0]["path"] = "/unrecorded"
    if damage == "unused":
        rows.append(rows[-1])
    if damage == "same_served":
        for row in rows:
            row["response"]["headers"]["x-omniroute-model"] = "same-model"
    if damage == "citation":
        answer = rows[0]["response"]["json"]["choices"][0]["message"]
        answer["content"] = json.dumps({"verdict": "met", "reason": "Generated wrong citation",
                                        "entry_ids": ["note:never-emitted"]})
    path = input_file(tmp_path, data)
    raw = path.read_bytes()
    originals = {p.name: p.read_bytes() for p in tmp_path.glob("*.json")}
    received, environments = [], []
    original = IsolatedServices._coverage

    def observe(self, service):
        environments.append(self)
        coverage = original(self, service)

        @web.middleware
        async def capture(request, handler):
            received.append(await request.json())
            return await coverage(request, handler)

        return capture

    monkeypatch.setattr(IsolatedServices, "_coverage", observe)
    report = await module.rejudge(tmp_path, tmp_path / "case.json", cfg, *MODELS, services=path)
    output = Path(report["rejudged"]["output"])
    assert report["status"] == ("PASS" if damage is None else "ERROR"), report
    assert report["evidence"] == {"status": "PASS", "source_only": "generated original receipt"}
    assert report["run_budget"] == {"generated_source": True}
    assert json.loads((output / "run_budget.json").read_text()) == report["run_budget"]
    assert report["rejudged"]["mode"] == "protocol-rehearsal"
    assert report["rejudged"]["behavior"] == "UNMEASURED"
    assert report["rejudged"]["execution"] == (
        "ERROR" if damage in {"route", "unused"} else "PASS")
    assert {name: (tmp_path / name).read_bytes() for name in originals} == originals
    assert environments and all(not env.active for env in environments)
    assert not getattr(socket.socket.connect, "_nora_eval_guard", False)
    capture = Path(report["rejudge_evidence"]["path"])
    events = read_events(capture)
    saved = capture / "external-services.json"
    assert saved.read_bytes() == raw
    assert saved.stat().st_mode & 0o777 == 0o600
    assert hashlib.sha256(raw).hexdigest() in (capture / "events.jsonl").read_text()
    requests = [json.loads(e["attributes"]["nora.model.request.body"]) for e in events
                if e.get("name") == "nora.model.request"]
    assert requests[:len(received)] == received
    assert requests and requests[0]["model"] == MODELS[0]
    if damage == "route":
        assert len(received) == report["rejudge_run_budget"]["requests_used"] == 1
        assert report["rejudge_evidence"]["status"] == "ERROR"
        assert any("external_response_not_recorded" in e for e in report["errors"])
    else:
        assert requests == received
        assert report["rejudge_run_budget"]["requests_used"] == 4
        assert [r["model"] for r in requests] == list(MODELS) * 2
        assert sum(e["kind"] == "transport_response" for e in events) == 4
        assert report["coverage"]["expected"] == report["coverage"]["observed"]
        assert report["coverage"]["expected"]["judge_answers"] == 4
        assert report["coverage"]["status"] == "PASS"
        if damage == "unused":
            assert report["rejudge_evidence"]["status"] == "ERROR"
            assert any("external_response_not_consumed" in e for e in report["errors"])
        else:
            assert report["rejudge_evidence"]["status"] == "PASS"
    saved_report = json.loads((output / "report.json").read_text())
    assert saved_report == json.loads(json.dumps(report))
    html = (output / "report.html").read_text()
    assert report["status"] in html and "judge_answers" in html
    assert "protocol-rehearsal" in html and "UNMEASURED" in html
    assert "Source evidence receipt" in html and "Rejudge capture" in html


@pytest.mark.parametrize("damage", [
    "missing_recording", "no_recording_file", "missing_wake", "manifest",
])
async def test_rejudge_refuses_before_configuration_and_dispatch(
    tmp_path, frozen_run, monkeypatch, damage,
):
    case, cfg, data = frozen_run
    if damage == "missing_recording":
        recording = data["corpora"]["cognition-rejudge"].pop(case.name)
        data["corpora"]["cognition-rejudge"]["another-case"] = recording
    if damage == "missing_wake":
        (tmp_path / f"wake-0-{case.wakes[0]}.json").unlink()
    if damage == "manifest":
        source = tmp_path / "report.json"
        report = json.loads(source.read_text())
        report["expected_cases"][0]["expectations"][0]["text"] = "Changed criterion"
        source.write_text(json.dumps(report))
    originals = {p.name: p.read_bytes() for p in tmp_path.glob("*.json")}

    def forbidden(*args, **kwargs):
        raise AssertionError("refused evidence reached configuration")

    monkeypatch.setattr(module, "load", forbidden)
    path = input_file(tmp_path, data) if damage != "no_recording_file" else None
    result = await module.rejudge(tmp_path, tmp_path / "case.json", cfg, *MODELS, services=path)
    assert result["status"] == "ERROR"
    assert result["rejudge_evidence"]["status"] == "ERROR"
    assert result["rejudged"]["execution"] == "ERROR"
    assert "refused evidence reached configuration" not in str(result)
    assert {name: (tmp_path / name).read_bytes() for name in originals} == originals
    version = Path(result["rejudged"]["output"])
    assert not await asyncio.to_thread(lambda: list(version.glob("*/events.jsonl")))
    assert not getattr(socket.socket.connect, "_nora_eval_guard", False)


async def test_failed_capture_keeps_the_new_report_and_frozen_judgments(
    tmp_path, frozen_run, monkeypatch,
):
    import os

    from nora.subject.eval_evidence import RunEvidence

    case, cfg, data = frozen_run
    result = RunEvidence.result
    write = os.write

    def unavailable(self, row):
        def disk_full(fd, body):
            if fd == self._fd:
                raise OSError("generated evidence disk failure")
            return write(fd, body)

        monkeypatch.setattr(os, "write", disk_full)
        return result(self, row)

    monkeypatch.setattr(RunEvidence, "result", unavailable)
    report = await module.rejudge(tmp_path, tmp_path / "case.json", cfg, *MODELS,
                                  services=input_file(tmp_path, data))
    assert report["status"] == report["rejudge_evidence"]["status"] == "ERROR"
    assert report["evidence"]["status"] == "PASS"
    assert report["rejudged"]["execution"] == "ERROR"
    assert report["rejudged"]["behavior"] == "UNMEASURED"
    assert "eval evidence write failed (OSError)" in str(report["errors"])
    output = Path(report["rejudged"]["output"])
    assert json.loads((output / "report.json").read_text()) == json.loads(json.dumps(report))
    assert (output / "report.html").is_file()
    for i, ident in enumerate(case.wakes):
        frozen = json.loads((output / f"wake-{i}-{ident}.json").read_text())
        assert len(frozen["expectations"][0]["answers"]) == 2
        assert "expectations" not in json.loads((tmp_path / f"wake-{i}-{ident}.json").read_text())
    events = read_events(Path(report["rejudge_evidence"]["path"]))
    assert sum(e["kind"] == "transport_response" for e in events) == 4
    assert not getattr(socket.socket.connect, "_nora_eval_guard", False)


async def test_cancellation_closes_owned_services_and_retains_error(
    tmp_path, frozen_run, monkeypatch,
):
    _case, cfg, data = frozen_run
    entered = asyncio.Event()
    environments = []
    coverage = IsolatedServices._coverage

    def observe(self, service):
        environments.append(self)
        return coverage(self, service)

    async def paused(*args):
        assert environments and all(env.active for env in environments)
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(IsolatedServices, "_coverage", observe)
    monkeypatch.setattr(module, "_judge_wakes", paused)
    original = (tmp_path / "report.json").read_bytes()
    pending = asyncio.create_task(module.rejudge(
        tmp_path, tmp_path / "case.json", cfg, *MODELS, services=input_file(tmp_path, data)))
    await asyncio.wait_for(entered.wait(), 5)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    versions = await asyncio.to_thread(lambda: list((tmp_path / "rejudgements").glob("*")))
    assert len(versions) == 1
    report = json.loads((versions[0] / "report.json").read_text())
    assert report["status"] == report["rejudge_evidence"]["status"] == "ERROR"
    assert "CancelledError" in str(report["errors"])
    assert (versions[0] / "report.html").is_file()
    assert (tmp_path / "report.json").read_bytes() == original
    assert all(not env.active for env in environments)
    assert not getattr(socket.socket.connect, "_nora_eval_guard", False)
