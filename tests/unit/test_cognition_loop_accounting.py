"""Run denominators survive missing rows, artifacts and judgments."""

import json
from dataclasses import asdict, replace

import pytest

from scripts.cognition_loop.accounting import manifest, reconcile, write_report
from scripts.cognition_loop.cases import generated_case
from scripts.cognition_loop.html import render


def complete_report(output):
    first = generated_case()
    second = replace(first, name="generated-second-case", wakes=(101, 202))
    report = {"status": "PASS", "expected_cases": manifest([first, second]),
              "evidence": {"status": "PASS", "causes": []},
              "cases": []}
    for case in (first, second):
        wakes = []
        for index, ident in enumerate(case.wakes):
            expectations = [{
                "expectation": {k: v for k, v in asdict(e).items() if k != "wake"},
                "verdict": "PASS", "answers": [{"model": "a"}, {"model": "b"}],
            } for e in case.expectations if e.wake == index]
            wakes.append({"recorded_wake_id": ident, "expectations": expectations,
                          "wake_id": 9000 + index,
                          "session_id": f"generated-session-{index}",
                          "at_ms": 1000 + index})
            (output / f"wake-{index}-{ident}.json").write_text(
                json.dumps({"diff": {}, "expectations": expectations,
                            "recorded_wake_id": ident, "unit": case.unit,
                            "at_ms": 1000 + index,
                            "trajectory": {"unit": case.unit,
                                           "at_ms": 1000 + index,
                                           "wake_id": 9000 + index,
                                           "session_id": f"generated-session-{index}"}}))
        report["cases"].append({"case": case.name, "unit": case.unit, "wakes": wakes})
    return report


@pytest.mark.parametrize("damage", [None, "case", "wake", "criterion", "answer", "artifact",
                                    "frozen", "identity", "duplicate", "manifest", "malformed",
                                    "frozen_unit", "frozen_instant", "frozen_step",
                                    "unproven", "recorded_wake", "actual_wake",
                                    "foreign_session", "partial_identity"])
def test_expected_denominators_do_not_shrink_with_missing_evidence(tmp_path, damage):
    report = complete_report(tmp_path)
    if damage == "case":
        report["cases"].pop()
    elif damage == "wake":
        report["cases"][0]["wakes"].pop()
    elif damage == "criterion":
        report["cases"][0]["wakes"][0]["expectations"].clear()
    elif damage == "answer":
        report["cases"][0]["wakes"][0]["expectations"][0]["answers"].pop()
    elif damage == "artifact":
        (tmp_path / "wake-0-11.json").unlink()
    elif damage == "frozen":
        (tmp_path / "wake-0-11.json").write_text('{"expectations": []}')
    elif damage == "identity":
        report["cases"][0]["wakes"][0]["recorded_wake_id"] = -1
    elif damage == "duplicate":
        report["cases"].append(report["cases"][0])
    elif damage == "manifest":
        del report["expected_cases"]
    elif damage == "malformed":
        report["cases"][0]["wakes"][0]["expectations"] = ["unreadable"]
    elif damage == "frozen_unit":
        frozen = json.loads((tmp_path / "wake-0-11.json").read_text())
        frozen["trajectory"]["unit"] = "another-unit"
        (tmp_path / "wake-0-11.json").write_text(json.dumps(frozen))
    elif damage == "frozen_instant":
        frozen = json.loads((tmp_path / "wake-0-11.json").read_text())
        frozen["trajectory"]["at_ms"] = -1
        (tmp_path / "wake-0-11.json").write_text(json.dumps(frozen))
    elif damage == "frozen_step":
        report["cases"][0]["wakes"][0]["step"] = "wake"
        frozen = json.loads((tmp_path / "wake-0-11.json").read_text())
        frozen["error"] = {"step": "advance", "message": "swapped artifact"}
        (tmp_path / "wake-0-11.json").write_text(json.dumps(frozen))
    elif damage == "unproven":
        (tmp_path / "wake-0-11.json").write_text('{}')
    elif damage == "recorded_wake":
        # A same-unit artifact frozen for a different recorded wake.
        frozen = json.loads((tmp_path / "wake-0-11.json").read_text())
        frozen["recorded_wake_id"] = 22
        (tmp_path / "wake-0-11.json").write_text(json.dumps(frozen))
    elif damage == "actual_wake":
        # The recorded wake matches but the replay's own wake is foreign.
        frozen = json.loads((tmp_path / "wake-0-11.json").read_text())
        frozen["trajectory"]["wake_id"] = -1
        (tmp_path / "wake-0-11.json").write_text(json.dumps(frozen))
    elif damage == "foreign_session":
        frozen = json.loads((tmp_path / "wake-0-11.json").read_text())
        frozen["trajectory"]["session_id"] = "someone-elses-session"
        (tmp_path / "wake-0-11.json").write_text(json.dumps(frozen))
    elif damage == "partial_identity":
        # Unit and instant agree; the recorded-wake identity was never frozen.
        frozen = json.loads((tmp_path / "wake-0-11.json").read_text())
        frozen.pop("recorded_wake_id")
        (tmp_path / "wake-0-11.json").write_text(json.dumps(frozen))
    if damage in ("criterion", "answer"):
        frozen = json.loads((tmp_path / "wake-0-11.json").read_text())
        frozen["expectations"] = report["cases"][0]["wakes"][0]["expectations"]
        (tmp_path / "wake-0-11.json").write_text(json.dumps(frozen))
    coverage = reconcile(report, tmp_path)
    assert coverage["status"] == ("PASS" if damage is None else "ERROR")
    if damage not in ("manifest", "malformed"):
        assert coverage["expected"] == {"cases": 2, "wakes": 4, "criteria": 4, "judge_answers": 8}
    if damage is None:
        assert coverage["observed"] == coverage["expected"] and coverage["errors"] == []
    else:
        assert coverage["errors"]


def test_json_and_html_use_the_same_failed_coverage(tmp_path):
    report = complete_report(tmp_path)
    report["cases"].pop()
    write_report(tmp_path, report)
    saved = json.loads((tmp_path / "report.json").read_text())
    assert saved["status"] == "ERROR"
    assert saved["coverage"]["expected"]["cases"] == 2
    assert saved["coverage"]["observed"]["cases"] == 1
    html = render(tmp_path).read_text()
    assert "case report denominator mismatch" in html
    assert "Expected coverage" in html


def test_the_saved_report_serializes_the_three_axes_once(tmp_path):
    """JSON and HTML read one `outcome` block: execution, measurement
    validity and behaviour as separate answers, never one rollup."""
    report = complete_report(tmp_path)
    write_report(tmp_path, report)
    saved = json.loads((tmp_path / "report.json").read_text())
    axes = saved["outcome"]
    assert axes["execution"]["status"] == "PASS"
    assert axes["execution"]["settled"] == 4
    assert axes["validity"]["status"] == "PASS"
    assert axes["behavior"]["status"] == "UNMEASURED"
    assert axes["behavior"]["verdicts"] == {"PASS": 4, "FAIL": 0, "REVIEW": 0, "ERROR": 0}
    html = render(tmp_path).read_text()
    for status in (axes["execution"]["status"], axes["validity"]["status"],
                   axes["behavior"]["status"]):
        assert status in html


def test_an_unsettled_wake_is_execution_unknown_not_pass(tmp_path):
    report = complete_report(tmp_path)
    report["cases"][0]["wakes"][1] = {
        "recorded_wake_id": report["cases"][0]["wakes"][1]["recorded_wake_id"],
        "at_ms": 1001, "step": "wake", "error": "the session ended failed"}
    frozen = json.loads((tmp_path / "wake-1-22.json").read_text())
    frozen["error"] = {"step": "wake", "message": "the session ended failed"}
    (tmp_path / "wake-1-22.json").write_text(json.dumps(frozen))
    write_report(tmp_path, report)
    saved = json.loads((tmp_path / "report.json").read_text())
    assert saved["outcome"]["execution"]["status"] == "ERROR"
    assert saved["outcome"]["execution"]["errored"] == 1
    assert saved["outcome"]["validity"]["status"] == "ERROR"


def test_a_settled_row_without_a_completion_signal_stays_unestablished(tmp_path):
    """A row with no session, no recorded no-session detail and no inspect
    claim carries no positive canonical completion — its execution axis is
    UNKNOWN and the report can never be a PASS."""
    report = complete_report(tmp_path)
    report["cases"][0]["wakes"][0].pop("session_id")
    report["cases"][0]["wakes"][0].pop("wake_id")
    frozen = json.loads((tmp_path / "wake-0-11.json").read_text())
    frozen["trajectory"].pop("session_id")
    frozen["trajectory"].pop("wake_id")
    (tmp_path / "wake-0-11.json").write_text(json.dumps(frozen))
    write_report(tmp_path, report)
    saved = json.loads((tmp_path / "report.json").read_text())
    assert saved["outcome"]["execution"]["status"] == "UNKNOWN"
    assert saved["outcome"]["execution"]["settled"] == 3
    assert saved["outcome"]["execution"]["unestablished"] == 1
    assert saved["outcome"]["validity"]["status"] == "PASS"
    assert saved["status"] == "ERROR"


def test_a_no_session_row_is_a_typed_silent_settlement(tmp_path):
    """The closed outcome's own no-session detail is the positive signal —
    no fake session id, and the axis is PASS only with it recorded."""
    report = complete_report(tmp_path)
    wake = report["cases"][0]["wakes"][0]
    wake.pop("session_id"), wake.pop("wake_id")
    wake["no_session"] = "empty agenda"
    frozen = json.loads((tmp_path / "wake-0-11.json").read_text())
    frozen["no_session"] = "empty agenda"
    frozen["trajectory"].pop("session_id")
    frozen["trajectory"].pop("wake_id")
    (tmp_path / "wake-0-11.json").write_text(json.dumps(frozen))
    write_report(tmp_path, report)
    saved = json.loads((tmp_path / "report.json").read_text())
    assert saved["outcome"]["execution"]["status"] == "PASS"
    assert saved["outcome"]["execution"]["settled"] == 4


def test_inspect_only_settles_on_the_inspections_own_evidence(tmp_path):
    """Inspect-only runs no session to settle: its claim is the
    inspection's assembled context. `model_calls: False` alone is not a
    completion signal."""
    report = complete_report(tmp_path)
    report["run"] = {"mode": "inspect-only"}
    for case in report["cases"]:
        for index, wake in enumerate(case["wakes"]):
            wake.pop("session_id"), wake.pop("wake_id")
            wake["model_calls"] = False
            trajectory = {
                "unit": case["unit"], "at_ms": 1000 + index,
                "blocks": {"system": "generated", "agenda": "generated",
                           "items": [], "stimuli": [], "instructions": None}}
            wake["trajectory"] = trajectory
            frozen = json.loads(
                (tmp_path / f"wake-{index}-{wake['recorded_wake_id']}.json").read_text())
            frozen["trajectory"].pop("session_id")
            frozen["trajectory"].pop("wake_id")
            (tmp_path / f"wake-{index}-{wake['recorded_wake_id']}.json").write_text(
                json.dumps(frozen))
    report["cases"][0]["wakes"][1]["trajectory"]["blocks"] = {}
    write_report(tmp_path, report)
    saved = json.loads((tmp_path / "report.json").read_text())
    assert saved["outcome"]["execution"]["status"] == "UNKNOWN"
    assert saved["outcome"]["execution"]["settled"] == 3
    assert saved["outcome"]["execution"]["unestablished"] == 1


def test_a_report_outcome_axis_unknown_blocks_the_saved_status(tmp_path):
    """Finalization gates required axes: an unestablished execution is
    never written beside a PASS, in JSON, HTML or the CLI state."""
    report = complete_report(tmp_path)
    report["cases"][0]["wakes"][0].pop("session_id")
    report["cases"][0]["wakes"][0].pop("wake_id")
    frozen = json.loads((tmp_path / "wake-0-11.json").read_text())
    frozen["trajectory"].pop("session_id")
    frozen["trajectory"].pop("wake_id")
    (tmp_path / "wake-0-11.json").write_text(json.dumps(frozen))
    write_report(tmp_path, report)
    saved = json.loads((tmp_path / "report.json").read_text())
    assert saved["status"] == "ERROR"
    assert saved["outcome"]["execution"]["status"] == "UNKNOWN"


def test_rejudge_preflight_allows_missing_judgments_but_keeps_case_denominator(tmp_path):
    report = complete_report(tmp_path)
    report["cases"][0]["wakes"][0]["expectations"] = []
    frozen = json.loads((tmp_path / "wake-0-11.json").read_text())
    frozen["expectations"] = []
    (tmp_path / "wake-0-11.json").write_text(json.dumps(frozen))
    assert reconcile(report, tmp_path, judgments=False)["status"] == "PASS"
    report["cases"].pop()
    assert reconcile(report, tmp_path, judgments=False)["status"] == "ERROR"
