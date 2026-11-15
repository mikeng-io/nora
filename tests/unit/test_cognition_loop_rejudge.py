"""A frozen wake's diff rebuilds the after-state the judges read."""

from pathlib import Path

import pytest

from scripts.cognition_loop.accounting import manifest
from scripts.cognition_loop.rejudge import frozen_diff
from scripts.cognition_loop.verdicts import JudgeAnswer


@pytest.fixture
def rejudge_services(tmp_path, example_config):
    import json

    from scripts.cognition_loop.cases import generated_case

    bindings = {"llm.base_url": "judge"}
    bindings.update({f"providers.{name}.base_url": "judge" for name in example_config.providers})
    path = tmp_path / "services.json"
    path.write_text(json.dumps({"version": 1, "corpora": {"cognition-rejudge": {
        generated_case().name: {"version": 1, "bindings": bindings, "services": {"judge": []}}
    }}}))
    return path


@pytest.mark.parametrize("missing", [0, 1, 2])
async def test_missing_frozen_wake_stops_before_judge_setup(monkeypatch, tmp_path, missing):
    import json
    from dataclasses import replace

    from scripts.cognition_loop import rejudge as module
    from scripts.cognition_loop.cases import generated_case, write_case

    case = replace(generated_case(), wakes=(101, 102, 103))
    case_path = tmp_path / "case.json"
    write_case(case, case_path)
    (tmp_path / "report.json").write_text(json.dumps({
        "expected_cases": manifest([case]),
        "status": "PASS", "cases": [{"case": case.name, "unit": case.unit, "wakes": [
            {"recorded_wake_id": ident, "at_ms": i}
            for i, ident in enumerate(case.wakes)]}]}))
    for index, ident in enumerate(case.wakes):
        if index != missing:
            (tmp_path / f"wake-{index}-{ident}.json").write_text(
                json.dumps({"recorded_wake_id": ident, "unit": case.unit,
                            "at_ms": index,
                            "trajectory": {"unit": case.unit,
                                           "session_id": None}}))
    original = (tmp_path / "report.json").read_bytes()
    def forbidden(*args):
        raise AssertionError("judge setup must not run")
    monkeypatch.setattr(module, "load", forbidden)
    monkeypatch.setattr(module, "render", lambda _: None)
    report = await module.rejudge(tmp_path, case_path, tmp_path / "config", "a", "b")
    assert report["status"] == "ERROR"
    assert (f"missing or invalid frozen wake: wake-{missing}-{case.wakes[missing]}.json"
            in report["cases"][0]["errors"])
    assert (tmp_path / "report.json").read_bytes() == original
    version = Path(report["rejudged"]["output"])
    assert json.loads((version / "report.json").read_text())["status"] == "ERROR"


def test_the_after_state_is_created_revised_and_unchanged_never_retired():
    frozen = {"diff": {"note": {
        "created": [{"status": "created", "entry_id": "n1", "after": {"t": "a"}}],
        "revised": [{"status": "revised", "entry_id": "n2",
                     "before": {"t": "x"}, "after": {"t": "y"}}],
        "retired": [{"status": "retired", "entry_id": "n3", "before": {"t": "z"}}],
        "unchanged": [{"status": "unchanged", "entry_id": "n4",
                       "before": {"t": "u"}, "after": {"t": "u"}}],
    }}}
    diff, after = frozen_diff(frozen)
    assert set(after.entries("note")) == {"n1", "n2", "n4"}
    assert after.entries("note")["n2"] == {"t": "y"}
    assert "note:n3" in diff.entry_ids()


@pytest.mark.parametrize("damage", [None, "row", "identity", "json", "criteria",
                                    "case_manifest", "missing_case", "malformed_manifest",
                                    "frozen_unit", "frozen_instant", "unproven"])
async def test_preflight_reconciles_rows_and_json_without_changing_originals(
    monkeypatch, tmp_path, damage, example_config, rejudge_services,
):
    import json
    from dataclasses import replace

    from scripts.cognition_loop import rejudge as module
    from scripts.cognition_loop.cases import generated_case, write_case
    from scripts.cognition_loop.verdicts import ExpectationVerdict

    case = generated_case()
    if damage == "criteria":
        case = replace(case, expectations=())
    case_path = tmp_path / "case.json"
    write_case(case, case_path)
    wakes = [{"recorded_wake_id": ident, "status": "PASS", "at_ms": i}
             for i, ident in enumerate(case.wakes)]
    if damage == "row":
        wakes.pop()
    if damage == "identity":
        wakes[0]["recorded_wake_id"] = -1
    (tmp_path / "report.json").write_text(json.dumps({
        "expected_cases": manifest([case]),
        "status": "PASS", "cases": [{"case": case.name, "unit": case.unit, "wakes": wakes}],
        "evidence": {"status": "PASS"}}))
    for i, ident in enumerate(case.wakes):
        frozen = {"diff": {}, "recorded_wake_id": ident,
                  "unit": case.unit, "at_ms": i,
                  "trajectory": {"unit": case.unit, "at_ms": i,
                                 "session_id": None}}
        if damage == "frozen_unit" and i == 0:
            frozen["trajectory"]["unit"] = "another-unit"
        if damage == "frozen_instant" and i == 0:
            frozen["trajectory"]["at_ms"] = -1
        if damage == "unproven" and i == 0:
            frozen = {}
        (tmp_path / f"wake-{i}-{ident}.json").write_text(
            "{" if damage == "json" and i == 0 else json.dumps(frozen))
    if damage == "case_manifest":
        write_case(replace(case, expectations=(replace(case.expectations[0], text="changed"),)),
                   case_path)
    if damage == "missing_case":
        path = tmp_path / "report.json"
        report = json.loads(path.read_text())
        report["expected_cases"] += manifest([
            replace(case, name="missing-case", wakes=(101, 202))])
        path.write_text(json.dumps(report))
    if damage == "malformed_manifest":
        path = tmp_path / "report.json"
        report = json.loads(path.read_text())
        report["expected_cases"] = [None]
        path.write_text(json.dumps(report))
    originals = {p.name: p.read_bytes() for p in tmp_path.glob("*.json")}
    calls, setups = [], []

    def setup(*args, **kwargs):
        setups.append(True)
        return example_config

    async def judge(_judges, _models, expectation, diff, after, trajectory, earlier):
        calls.append((expectation.kind, expectation.wake))
        return ExpectationVerdict(verdict="PASS", answers=(
            JudgeAnswer("met", model="a"), JudgeAnswer("met", model="b")))

    monkeypatch.setattr(module, "load", setup)
    monkeypatch.setattr(module, "Judges", lambda *_: object())
    monkeypatch.setattr(module, "judge_expectation", judge)
    monkeypatch.setattr(module, "render", lambda _: None)
    result = await module.rejudge(tmp_path, case_path, tmp_path / "config", "a", "b",
                                  services=rejudge_services)
    assert result["status"] == ("PASS" if damage is None else "ERROR")
    assert calls == ([(e.kind, e.wake) for e in case.expectations] if damage is None else [])
    assert setups == ([True] if damage is None else [])
    assert {name: (tmp_path / name).read_bytes() for name in originals} == originals


@pytest.mark.parametrize("damage", ["unit", "recorded_wake", "actual_wake",
                                    "session", "partial"])
async def test_a_frozen_artifact_that_is_not_this_wake_is_never_judged(
    monkeypatch, tmp_path, damage,
):
    """`_judge_wakes` must not judge a contradictory or unproven artifact
    into apparent correctness: a foreign wake/session, a same-unit swap
    for a different recorded wake, or an artifact that only partially
    proves itself all stay ERROR with no judge call spent — while a
    sound artifact still is."""
    import json

    from scripts.cognition_loop import rejudge as module
    from scripts.cognition_loop.cases import generated_case
    from scripts.cognition_loop.verdicts import ExpectationVerdict

    case = generated_case()
    row = {"case": case.name, "unit": case.unit, "errors": [], "wakes": [
        {"recorded_wake_id": ident, "status": "PASS", "at_ms": 1000 + i,
         "wake_id": 9000 + i, "session_id": f"generated-session-{i}"}
        for i, ident in enumerate(case.wakes)]}
    for i, ident in enumerate(case.wakes):
        frozen = {"diff": {}, "recorded_wake_id": ident,
                  "unit": case.unit, "at_ms": 1000 + i,
                  "trajectory": {
                      "unit": case.unit, "at_ms": 1000 + i,
                      "wake_id": 9000 + i,
                      "session_id": f"generated-session-{i}"}}
        if i == 0:
            if damage == "unit":
                frozen["trajectory"]["unit"] = "another-unit"
            elif damage == "recorded_wake":
                frozen["recorded_wake_id"] = 999
            elif damage == "actual_wake":
                frozen["trajectory"]["wake_id"] = -1
            elif damage == "session":
                frozen["trajectory"]["session_id"] = "someone-elses-session"
            elif damage == "partial":
                frozen.pop("recorded_wake_id")
        (tmp_path / f"wake-{i}-{ident}.json").write_text(json.dumps(frozen))
    calls = []

    async def judge(*a, **k):
        calls.append(1)
        return ExpectationVerdict(verdict="PASS", answers=(
            JudgeAnswer("met", model="a"), JudgeAnswer("met", model="b")))

    monkeypatch.setattr(module, "judge_expectation", judge)
    await module._judge_wakes(tmp_path, row, case, object(), ("a", "b"))
    assert calls == [1]                    # only the sound wake was judged
    assert row["wakes"][0]["status"] == "ERROR"
    assert row["wakes"][0]["expectations"] == []
    assert any("frozen wake identity " in e for e in row["errors"])
    assert row["wakes"][1]["status"] == "PASS"


def test_rejudging_keeps_a_case_error_and_a_failed_evidence_receipt():
    from scripts.cognition_loop.rejudge import case_status, run_status_after

    assert case_status({"errors": ["advance failed"]}, ["PASS"]) == "ERROR"
    assert case_status({}, ["PASS"]) == "PASS"
    assert case_status({}, []) == "ERROR"
    passed = {"cases": [{"status": "PASS"}], "evidence": {"status": "PASS"}}
    assert run_status_after(passed) == "PASS"
    assert run_status_after({**passed, "evidence": {"status": "ERROR"}}) == "ERROR"
    assert run_status_after({**passed, "errors": [{"step": "room_directory"}]}) == "ERROR"


def test_rejudging_recomputes_the_derived_wake_error():
    from scripts.cognition_loop.rejudge import case_status
    from scripts.cognition_loop.verdicts import WAKE_ERRED

    assert case_status({"errors": [WAKE_ERRED]}, ["PASS"]) == "PASS"
    assert case_status({"errors": [WAKE_ERRED, "advance failed"]}, ["PASS"]) == "ERROR"


def test_a_report_error_with_no_frozen_after_state_stays_unmeasured():
    from scripts.cognition_loop.rejudge import unmeasured

    no_diff = {"error": {"step": "report", "message": "snapshot failed"}}
    assert unmeasured(no_diff, {"step": "report", "status": "ERROR"})
    # Positive control: a report error after the diff was frozen is judged again.
    assert not unmeasured({**no_diff, "diff": {"note": {}}}, {"step": "report"})
    assert unmeasured({"error": {"step": "advance"}, "diff": {}}, {})


async def test_an_unmeasured_wake_spends_no_judge_call(
    monkeypatch, tmp_path, example_config, rejudge_services,
):
    """A wake whose step failed before it settled stays ERROR on rejudge
    and its expectations are never judged; a settled wake still is."""
    import json

    from scripts.cognition_loop import rejudge as module
    from scripts.cognition_loop.cases import generated_case, write_case
    from scripts.cognition_loop.verdicts import ExpectationVerdict

    case = generated_case()
    case_path = tmp_path / "case.json"
    write_case(case, case_path)
    wakes = [{"status": "PASS", "expectations": [], "at_ms": 1000 + i,
              "recorded_wake_id": ident} for i, ident in enumerate(case.wakes)]
    wakes[1]["status"] = "ERROR"
    wakes[1]["step"] = "wake"
    (tmp_path / "report.json").write_text(json.dumps({
        "expected_cases": manifest([case]),
        "cases": [{"case": case.name, "unit": case.unit, "wakes": wakes,
                   "errors": ["a wake ended in ERROR"]}],
        "evidence": {"status": "PASS"}}))
    (tmp_path / f"wake-0-{case.wakes[0]}.json").write_text(json.dumps({
        "diff": {}, "recorded_wake_id": case.wakes[0],
        "unit": case.unit, "at_ms": 1000,
        "trajectory": {"blocks": {"agenda": "generated source 0"},
                       "unit": case.unit, "at_ms": 1000,
                       "session_id": None}}))
    (tmp_path / f"wake-1-{case.wakes[1]}.json").write_text(json.dumps({
        "error": {"step": "wake",
                  "message": "the session ended failed: capture interrupted"},
        "recorded_wake_id": case.wakes[1], "unit": case.unit,
        "at_ms": 1001,
        "trajectory": {"session_id": None}}))

    judged = []

    async def judge(*a, **k):
        judged.append(1)
        return ExpectationVerdict(verdict="PASS", answers=(
            JudgeAnswer("met", model="a"), JudgeAnswer("met", model="b")))

    monkeypatch.setattr(module, "load", lambda *a, **k: example_config)
    monkeypatch.setattr(module, "Judges", lambda *_: object())
    monkeypatch.setattr(module, "judge_expectation", judge)
    monkeypatch.setattr(module, "render", lambda _: None)
    report = await module.rejudge(tmp_path, case_path, tmp_path / "config", "a", "b",
                                  services=rejudge_services)
    assert judged == [1]                    # only the settled wake was judged
    assert report["cases"][0]["wakes"][1]["status"] == "ERROR"
    assert report["cases"][0]["wakes"][1]["expectations"] == []
    assert report["status"] == "ERROR"


async def test_rejudging_preserves_the_reason_an_answer_cannot_pass(
    monkeypatch, tmp_path, example_config, rejudge_services,
):
    import json

    from scripts.cognition_loop import rejudge as module
    from scripts.cognition_loop.cases import generated_case, write_case
    from scripts.cognition_loop.verdicts import ExpectationVerdict

    case = generated_case()
    case_path = tmp_path / "case.json"
    write_case(case, case_path)
    wakes = [{"status": "PASS", "expectations": [], "at_ms": 1000 + i,
              "recorded_wake_id": ident} for i, ident in enumerate(case.wakes)]
    (tmp_path / "report.json").write_text(json.dumps({
        "expected_cases": manifest([case]),
        "cases": [{"case": case.name, "unit": case.unit, "wakes": wakes}],
        "evidence": {"status": "PASS"}}))
    for index, ident in enumerate(case.wakes):
        (tmp_path / f"wake-{index}-{ident}.json").write_text(json.dumps({
            "diff": {}, "recorded_wake_id": ident,
            "unit": case.unit, "at_ms": 1000 + index,
            "trajectory": {
                "blocks": {"agenda": f"generated source {index}"},
                "unit": case.unit, "at_ms": 1000 + index,
                "session_id": None}}))

    # Assertions inside the fake would be swallowed by rejudge's fail-closed
    # except; the evidence is recorded and checked after the call instead.
    seen = {}

    async def judge(_judges, _models, expectation, diff, after, trajectory, earlier):
        seen[expectation.wake] = (trajectory, tuple(earlier))
        return ExpectationVerdict(
            verdict="ERROR" if expectation.kind == "note" else "PASS", answers=(),
            detail="a decidable answer needs a cited entry" if expectation.kind == "note" else "")

    monkeypatch.setattr(module, "load", lambda *a, **k: example_config)
    monkeypatch.setattr(module, "Judges", lambda *_: object())
    monkeypatch.setattr(module, "judge_expectation", judge)
    monkeypatch.setattr(module, "render", lambda _: None)
    report = await module.rejudge(tmp_path, case_path, tmp_path / "config", "a", "b",
                                  services=rejudge_services)
    assert report["status"] == "ERROR"
    exp = report["cases"][0]["wakes"][0]["expectations"][0]
    assert exp["detail"] == "a decidable answer needs a cited entry"
    frozen = json.loads((Path(report["rejudged"]["output"]) /
                         f"wake-0-{case.wakes[0]}.json").read_text())
    assert frozen["expectations"][0]["detail"] == exp["detail"]
    original = json.loads((tmp_path / f"wake-0-{case.wakes[0]}.json").read_text())
    assert "expectations" not in original
    # The judge saw each clock from the report metadata and the case's unit;
    # old freezes never captured the room scope: null, never inferred.
    wake0, wake1 = seen[0], seen[1]
    assert wake0[0]["at_ms"] == 1000
    assert wake1[0]["at_ms"] == 1001
    assert wake1[0].get("unit") == case.unit
    assert wake1[0].get("unit_rooms") is None
    # History is only the earlier frozen wakes — never this wake or a later one.
    assert wake0[1] == ()
    assert len(wake1[1]) == 1
    prior_diff, prior_traj = wake1[1][0]
    assert prior_traj["at_ms"] == 1000
    assert prior_traj["blocks"]["agenda"] == "generated source 0"
    assert prior_traj.get("unit") == case.unit
    assert prior_traj.get("unit_rooms") is None
    assert isinstance(prior_diff.kinds, dict)
