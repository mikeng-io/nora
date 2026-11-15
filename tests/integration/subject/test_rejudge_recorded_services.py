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


@pytest.mark.parametrize("damage", [
    "terminal", "settlement", "identity", "antecedent",
])
async def test_rejudge_prerequisite_gap_spends_no_judge_on_the_affected(
    tmp_path, frozen_run, monkeypatch, damage,
):
    """The real judge door: a torn frozen occasion is refused by name
    BEFORE any HTTP — the sound prefix still judges through the real
    RecordedServices, the affected request count is exactly zero."""
    case, cfg, data = frozen_run
    frozen_path = tmp_path / f"wake-1-{case.wakes[1]}.json"
    frozen = json.loads(frozen_path.read_text())
    if damage in ("terminal", "settlement"):
        # A session-bound wake whose own settlement or terminal capture is
        # absent: identity stays current (the row and the artifact agree on
        # the session) so the refusal lands at the judge door, not the
        # denominator preflight.
        source = tmp_path / "report.json"
        raw_report = json.loads(source.read_text())
        raw_report["cases"][0]["wakes"][1]["session_id"] = "s-7"
        source.write_text(json.dumps(raw_report))
        if damage == "terminal":
            frozen["trajectory"].update(
                session_id="s-7", settlement={"status": "observed"})
        else:
            frozen["trajectory"].update(session_id="s-7")
    elif damage == "identity":
        frozen["unit"] = "foreign-unit"
    else:  # antecedent: tear the FIRST wake's evidence
        first = tmp_path / f"wake-0-{case.wakes[0]}.json"
        earlier = json.loads(first.read_text())
        earlier["trajectory"]["effects"] = {"status": "gone"}
        first.write_text(json.dumps(earlier))
    if damage != "antecedent":
        frozen_path.write_text(json.dumps(frozen))
    path = input_file(tmp_path, data)
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
    report = await module.rejudge(tmp_path, tmp_path / "case.json", cfg,
                                  *MODELS, services=path)
    assert report["status"] == "ERROR"
    if damage == "antecedent":
        # The torn antecedent never judges, and the sound wake that
        # would inherit it refuses by name — zero requests at all.
        assert received == []
    elif damage == "identity":
        # A foreign artifact is refused at the denominator before any
        # door opens: still zero judge HTTP, and the cause names the
        # mismatch — never a silent empty history.
        assert received == []
        assert any("identity mismatch" in e for e in report["errors"])
        return
    else:
        # Exactly the sound prefix: one wake, one expectation, both
        # aliases — the affected wake spends nothing.
        assert len(received) == 2
        assert {r["model"] for r in received} == set(MODELS)
    output = Path(report["rejudged"]["output"])
    torn = json.loads(
        (output / f"wake-1-{case.wakes[1]}.json").read_text())
    if damage != "antecedent":
        assert (torn.get("error") or {}).get("step") == "evidence"
        assert torn["expectations"] == []
        row = next(c for c in report["cases"] if c["case"] == case.name)
        assert row["wakes"][0]["status"] == "PASS"
        assert row["wakes"][1]["status"] == "ERROR"
        assert any("wake 1" in e for e in row.get("errors") or [])
    else:
        row = next(c for c in report["cases"] if c["case"] == case.name)
        assert row["wakes"][0]["status"] == "ERROR"
        assert row["wakes"][1]["status"] == "ERROR"
        assert any("earlier occasion" in e or "wake 1" in e
                   for e in row.get("errors") or [])
    assert environments and all(not env.active for env in environments)


@pytest.fixture
def behavior_run(tmp_path, example_config_path, monkeypatch):
    """A frozen version-2 run: two produced turns, one behavior claim on
    the second, sound artifacts a rejudge can stand on."""
    from nora.subject import sandbox
    from scripts.cognition_loop.cases import generated_behavior_case

    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated-only")
    monkeypatch.setattr(sandbox, "EVAL_HOST_LOCK", tmp_path / "eval.lock")
    case = generated_behavior_case()
    write_case(case, tmp_path / "case.json")
    turns = [
        {"turn": 0, "input": case.turns[0], "status": "PASS",
         "at_ms": 1000, "outcome": "recorded"},
        {"turn": 1, "input": case.turns[1], "status": "PASS",
         "at_ms": 2000, "outcome": "delivered"},
    ]
    report = {"status": "PASS", "expected_cases": manifest([case]),
              "evidence": {"status": "PASS",
                           "source_only": "generated original receipt"},
              "run_budget": {"generated_source": True},
              "cases": [{"case": case.name, "unit": case.unit,
                         "status": "PASS", "wakes": [], "turns": turns}]}
    (tmp_path / "report.json").write_text(json.dumps(report))
    (tmp_path / "run_budget.json").write_text(
        json.dumps(report["run_budget"]))
    for index, turn in enumerate(turns):
        delivered = turn["outcome"] == "delivered"
        (tmp_path / f"turn-{index}.json").write_text(json.dumps({
            "turn": index, "unit": case.unit, "at_ms": turn["at_ms"],
            "declared_input": {"text": case.turns[index],
                               "event_id": f"generated-{index}",
                               "at_ms": turn["at_ms"]},
            "error": None,
            "diff": {},
            "trajectory": {
                "turn": index, "session_id": None, "unit": case.unit,
                "at_ms": turn["at_ms"],
                "declared_input": {"text": case.turns[index],
                                   "event_id": f"generated-{index}"},
                "bound": {"event_id": f"generated-{index}",
                          "stimulus_id": 5 + index,
                          "wake_id": None, "session_id": None,
                          "wake_outcome": None, "session_conflict": None,
                          "attempt_traces": [f"t-attempt-{index}"],
                          "window": {"events": 3,
                                     "orders": [index * 10 + 1,
                                                index * 10 + 3]}},
                "model_calls": ([{"model": "generated-subject",
                                 "messages": [
                                     {"role": "system",
                                      "content": "generated instructions"},
                                     {"role": "user",
                                      "content": case.turns[index]}]}]
                                if delivered else []),
                "effects": {"status": "observed",
                            "items": ([{"kind": "delivery",
                                       "ref": f"delivery:b:{index}"}]
                                      if delivered else []),
                            "tools": []},
                "settlement": {"status": "observed", "event": "done"},
                "turn_outcome": (
                    {"status": "delivered",
                     "said": ["the venue is Cedar Hall"]}
                    if delivered else
                    {"status": "recorded",
                     "decision": {"engage": False,
                                  "reason": "untagged"}}),
                **({"final_response": {"status": "observed",
                                      "text": "the venue is Cedar Hall"}}
                   if delivered else {}),
            }}))
    cfg = tmp_path / "config.toml"
    cfg.write_bytes(example_config_path.read_bytes())
    loaded = module.load(cfg, require_secrets=False)
    bindings = {"llm.base_url": "judge"}
    bindings.update({f"providers.{name}.base_url": "judge"
                     for name in loaded.providers})
    rows = []
    for alias in MODELS:
        rows.append({"method": "POST", "path": "/v1/chat/completions",
                     "response": {
                         "status": 200,
                         "headers": {"x-omniroute-model": alias + "-served"},
                         "json": {
                             "id": f"generated-{alias}",
                             "object": "chat.completion", "created": 1,
                             "model": alias,
                             "choices": [{"index": 0, "finish_reason": "stop",
                                          "message": {
                                              "role": "assistant",
                                              "content": json.dumps({
                                                  "verdict": "met",
                                                  "reason": "generated",
                                                  "entry_ids": [
                                                      "delivery:b:1"]})}}],
                             "usage": {"prompt_tokens": 20,
                                       "completion_tokens": 10,
                                       "total_tokens": 30}}}})
    services = {"version": 1, "corpora": {"cognition-rejudge": {case.name: {
        "version": 1, "bindings": bindings, "services": {"judge": rows}}}}}
    return case, cfg, services


async def test_rejudge_version2_judges_turn_artifacts_through_real_doors(
    tmp_path, behavior_run, monkeypatch,
):
    """A version-2 case rejudges its frozen turn artifacts: the turn's
    declared input, typed outcome and effects reach the real judge seam
    — the sound turn's expectation gets both aliases, no wake is needed."""
    case, cfg, services = behavior_run
    path = input_file(tmp_path, services)
    received = []
    original = IsolatedServices._coverage

    def observe(self, service):
        coverage = original(self, service)

        @web.middleware
        async def capture(request, handler):
            received.append(await request.json())
            return await coverage(request, handler)

        return capture

    monkeypatch.setattr(IsolatedServices, "_coverage", observe)
    result = await module.rejudge(tmp_path, tmp_path / "case.json", cfg,
                                  *MODELS, services=path)
    assert result["status"] == "PASS", result
    # One behavior expectation on turn 1: both aliases, nothing for
    # the turn with no anchored claim — the denominator is honest.
    assert len(received) == 2
    assert {r["model"] for r in received} == set(MODELS)
    output = Path(result["rejudged"]["output"])
    row = next(c for c in result["cases"] if c["case"] == case.name)
    assert row["turns"][1]["status"] == "PASS"
    (expectation,) = row["turns"][1]["expectations"]
    assert len(expectation["answers"]) == 2
    frozen = json.loads((output / "turn-1.json").read_text())
    assert frozen["expectations"][0]["verdict"] == "PASS"


@pytest.mark.parametrize("damage", [
    "identity", "settlement", "antecedent", "foreign_bound",
    "conflicting_session", "missing_decision", "declared_event",
])
async def test_rejudge_version2_torn_turn_artifact_spends_no_judge(
    tmp_path, behavior_run, monkeypatch, damage,
):
    """The real turn door is fail-closed: a foreign or torn frozen turn
    artifact is refused by name before any HTTP — the recording proves
    zero affected judge requests ever left."""
    _case, cfg, services = behavior_run
    if damage == "identity":
        torn = tmp_path / "turn-1.json"
        frozen = json.loads(torn.read_text())
        frozen["unit"] = "foreign-unit"
        torn.write_text(json.dumps(frozen))
    elif damage == "settlement":
        torn = tmp_path / "turn-1.json"
        frozen = json.loads(torn.read_text())
        frozen["trajectory"]["settlement"] = {"status": "gone"}
        torn.write_text(json.dumps(frozen))
    elif damage == "foreign_bound":
        # Another occasion's binding claims this artifact.
        torn = tmp_path / "turn-1.json"
        frozen = json.loads(torn.read_text())
        frozen["trajectory"]["bound"]["event_id"] = "generated-9"
        torn.write_text(json.dumps(frozen))
    elif damage == "conflicting_session":
        # The frozen binding and the trajectory disagree on the session
        # the turn's stimulus settled to — conflict, never "latest wins".
        torn = tmp_path / "turn-1.json"
        frozen = json.loads(torn.read_text())
        frozen["trajectory"]["bound"]["session_id"] = 9
        torn.write_text(json.dumps(frozen))
    elif damage == "missing_decision":
        # Settled evidence with no production decision or output cannot
        # read as restraint — the middle is unconfirmed, never judged.
        torn = tmp_path / "turn-1.json"
        frozen = json.loads(torn.read_text())
        frozen["trajectory"]["turn_outcome"] = {"status": "unconfirmed"}
        torn.write_text(json.dumps(frozen))
    elif damage == "declared_event":
        # An input with no canonical admission identity is unbound.
        torn = tmp_path / "turn-1.json"
        frozen = json.loads(torn.read_text())
        frozen["trajectory"]["declared_input"].pop("event_id")
        torn.write_text(json.dumps(frozen))
    else:  # antecedent: the earlier turn's evidence cannot stand
        earlier = tmp_path / "turn-0.json"
        frozen = json.loads(earlier.read_text())
        frozen["trajectory"]["effects"] = {"status": "gone"}
        earlier.write_text(json.dumps(frozen))
    path = input_file(tmp_path, services)
    received = []
    original = IsolatedServices._coverage

    def observe(self, service):
        coverage = original(self, service)

        @web.middleware
        async def capture(request, handler):
            received.append(await request.json())
            return await coverage(request, handler)

        return capture

    monkeypatch.setattr(IsolatedServices, "_coverage", observe)
    result = await module.rejudge(tmp_path, tmp_path / "case.json", cfg,
                                  *MODELS, services=path)
    assert result["status"] == "ERROR"
    # The only anchored claim lives on turn 1 — every damage lands before
    # its door: exactly zero judge requests, never a silent empty history.
    assert received == []
    row = next(c for c in result["cases"] if c["case"] == _case.name)
    errors = " ".join(row.get("errors") or [])
    errors += json.dumps(result.get("errors") or "")
    assert errors


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
