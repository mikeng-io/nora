"""A frozen wake's diff rebuilds the after-state the judges read."""

from scripts.cognition_loop.rejudge import frozen_diff


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


async def test_rejudging_preserves_the_reason_an_answer_cannot_pass(monkeypatch, tmp_path):
    import json

    from scripts.cognition_loop import rejudge as module
    from scripts.cognition_loop.cases import generated_case, write_case
    from scripts.cognition_loop.verdicts import ExpectationVerdict

    case = generated_case()
    case_path = tmp_path / "case.json"
    write_case(case, case_path)
    wakes = [{"status": "PASS", "expectations": []} for _ in case.wakes]
    (tmp_path / "report.json").write_text(json.dumps({
        "cases": [{"case": case.name, "wakes": wakes}], "evidence": {"status": "PASS"}}))
    for index, ident in enumerate(case.wakes):
        (tmp_path / f"wake-{index}-{ident}.json").write_text(json.dumps({"diff": {}}))

    async def judge(_judges, _models, expectation, *_args):
        return ExpectationVerdict(
            verdict="ERROR" if expectation.kind == "note" else "PASS", answers=(),
            detail="a decidable answer needs a cited entry" if expectation.kind == "note" else "")

    monkeypatch.setattr(module, "load", lambda _: None)
    monkeypatch.setattr(module, "Judges", lambda *_: object())
    monkeypatch.setattr(module, "judge_expectation", judge)
    monkeypatch.setattr(module, "render", lambda _: None)
    report = await module.rejudge(tmp_path, case_path, tmp_path / "config", "a", "b")
    assert report["status"] == "ERROR"
    exp = report["cases"][0]["wakes"][0]["expectations"][0]
    assert exp["detail"] == "a decidable answer needs a cited entry"
    frozen = json.loads((tmp_path / f"wake-0-{case.wakes[0]}.json").read_text())
    assert frozen["expectations"][0]["detail"] == exp["detail"]
