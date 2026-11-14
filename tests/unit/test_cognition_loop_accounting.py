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
    report = {"status": "PASS", "expected_cases": manifest([first, second]), "cases": []}
    for case in (first, second):
        wakes = []
        for index, ident in enumerate(case.wakes):
            expectations = [{
                "expectation": {k: v for k, v in asdict(e).items() if k != "wake"},
                "verdict": "PASS", "answers": [{"model": "a"}, {"model": "b"}],
            } for e in case.expectations if e.wake == index]
            wakes.append({"recorded_wake_id": ident, "expectations": expectations})
            (output / f"wake-{index}-{ident}.json").write_text(
                json.dumps({"diff": {}, "expectations": expectations}))
        report["cases"].append({"case": case.name, "unit": case.unit, "wakes": wakes})
    return report


@pytest.mark.parametrize("damage", [None, "case", "wake", "criterion", "answer", "artifact",
                                    "frozen", "identity", "duplicate", "manifest", "malformed"])
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
    if damage in ("criterion", "answer"):
        (tmp_path / "wake-0-11.json").write_text(json.dumps({
            "expectations": report["cases"][0]["wakes"][0]["expectations"]}))
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


def test_rejudge_preflight_allows_missing_judgments_but_keeps_case_denominator(tmp_path):
    report = complete_report(tmp_path)
    report["cases"][0]["wakes"][0]["expectations"] = []
    assert reconcile(report, tmp_path, judgments=False)["status"] == "PASS"
    report["cases"].pop()
    assert reconcile(report, tmp_path, judgments=False)["status"] == "ERROR"
