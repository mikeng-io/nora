"""Run denominators survive missing rows, artifacts and judgments."""

import json
from dataclasses import asdict, replace

import pytest

from scripts.cognition_loop.accounting import manifest, reconcile, write_report
from scripts.cognition_loop.cases import (
    Case,
    DeclaredTurn,
    generated_case,
)
from scripts.cognition_loop.html import render
from tests.unit.test_journey_inputs import (
    SENT,
    _fields,
    _foreign_fields,
    _foreign_input,
    _recorded_input,
)


def _outcome_item(unit, wake_id, session_id, *, outcome="closed", at_ms,
                  ref=True):
    """A generated canonical `cognition_wake_outcome` effect in `read_effects` shape."""
    key = {"id": wake_id * 10 + 1}
    return {
        "ref": ("wake:cognition_wake_outcome:" + json.dumps(
            key, sort_keys=True, separators=(",", ":"))) if ref else None,
        "kind": "wake", "source": "cognition_wake_outcome", "key": key,
        "at": "2026-01-01T00:00:00+00:00", "operation": "append",
        "row": {"id": key["id"], "wake_id": wake_id, "unit": unit,
                "session_id": session_id, "outcome": outcome, "detail": "",
                "at_ms": at_ms, "cause": "none",
                "recorded_at": "2026-01-01T00:00:00+00:00"},
    }


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
            trajectory = {"unit": case.unit, "at_ms": 1000 + index,
                          "wake_id": 9000 + index,
                          "session_id": 7000 + index,
                          "effects": {
                              "status": "observed", "unit": case.unit,
                              "items": [_outcome_item(
                                  case.unit, 9000 + index, 7000 + index,
                                  at_ms=1000 + index)],
                              "tools": []}}
            wakes.append({"recorded_wake_id": ident, "expectations": expectations,
                          "wake_id": 9000 + index,
                          "session_id": 7000 + index,
                          "at_ms": 1000 + index,
                          "trajectory": trajectory})
            (output / f"wake-{index}-{ident}.json").write_text(
                json.dumps({"diff": {}, "expectations": expectations,
                            "recorded_wake_id": ident, "unit": case.unit,
                            "at_ms": 1000 + index,
                            "trajectory": trajectory}))
        report["cases"].append({"case": case.name, "unit": case.unit, "wakes": wakes})
    return report


@pytest.mark.parametrize("damage", [None, "case", "wake", "criterion", "answer", "artifact",
                                    "frozen", "identity", "duplicate", "manifest", "malformed",
                                    "frozen_unit", "frozen_instant", "frozen_step",
                                    "unproven", "recorded_wake", "actual_wake",
                                    "foreign_session", "partial_identity",
                                    "typed_identity", "unproven_session",
                                    "undeclared_instant"])
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
        # The artifact still proves its identity; only its frozen
        # judgments disagree with the report row.
        frozen = json.loads((tmp_path / "wake-0-11.json").read_text())
        frozen["expectations"] = []
        (tmp_path / "wake-0-11.json").write_text(json.dumps(frozen))
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
    elif damage == "typed_identity":
        # `11.0 == 11` but `float is not int`: identity is typed, never
        # equality alone — the same reason `True` is not `1`.
        frozen = json.loads((tmp_path / "wake-0-11.json").read_text())
        frozen["recorded_wake_id"] = float(
            report["cases"][0]["wakes"][0]["recorded_wake_id"])
        (tmp_path / "wake-0-11.json").write_text(json.dumps(frozen))
    elif damage == "unproven_session":
        # A session-less row is proved by the trajectory's explicit None —
        # an absent key is unproven, never a silent pass.
        report["cases"][0]["wakes"][0].pop("session_id")
        report["cases"][0]["wakes"][0]["trajectory"].pop("session_id")
        frozen = json.loads((tmp_path / "wake-0-11.json").read_text())
        frozen["trajectory"].pop("session_id")
        (tmp_path / "wake-0-11.json").write_text(json.dumps(frozen))
    elif damage == "undeclared_instant":
        # The row never declared the instant: the artifact's cannot be
        # verified, so the identity stays unknown.
        report["cases"][0]["wakes"][0].pop("at_ms")
        report["cases"][0]["wakes"][0]["trajectory"].pop("at_ms")
    if damage in ("criterion", "answer"):
        frozen = json.loads((tmp_path / "wake-0-11.json").read_text())
        frozen["expectations"] = report["cases"][0]["wakes"][0]["expectations"]
        (tmp_path / "wake-0-11.json").write_text(json.dumps(frozen))
    coverage = reconcile(report, tmp_path)
    assert coverage["status"] == ("PASS" if damage is None else "ERROR")
    if damage not in ("manifest", "malformed"):
        assert coverage["expected"] == {"cases": 2, "wakes": 4, "turns": 0,
                                        "criteria": 4, "judge_answers": 8}
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


@pytest.mark.parametrize("mode", ["protocol-rehearsal", "behavioral-evaluation"])
@pytest.mark.parametrize("verdict", ["PASS", "FAIL", "REVIEW"])
def test_the_declared_measurement_reaches_cognition_json_and_html(tmp_path, mode, verdict):
    report = complete_report(tmp_path)
    report.update(status=verdict, run={"measurement": mode})
    wake = report["cases"][0]["wakes"][0]
    wake["expectations"][0]["verdict"] = verdict
    path = tmp_path / "wake-0-11.json"
    frozen = json.loads(path.read_text())
    frozen["expectations"] = wake["expectations"]
    path.write_text(json.dumps(frozen))
    write_report(tmp_path, report)
    saved = json.loads((tmp_path / "report.json").read_text())
    expected = verdict if mode == "behavioral-evaluation" else "UNMEASURED"
    assert saved["coverage"]["status"] == "PASS"
    assert saved["outcome"]["execution"]["status"] == "PASS"
    assert saved["outcome"]["validity"]["status"] == "PASS"
    assert saved["status"] == verdict
    assert saved["behavior"] == saved["run"]["behavior"] == expected
    assert saved["outcome"]["behavior"]["status"] == expected
    assert saved["outcome"]["behavior"]["scope"] == mode
    html = render(tmp_path).read_text()
    assert mode in html
    assert f"<th>behaviour</th><td>{expected}</td>" in html


@pytest.mark.parametrize("receipt,expected", [(None, "UNKNOWN"), ("ERROR", "ERROR")])
def test_cognition_behavior_requires_valid_evidence(tmp_path, receipt, expected):
    report = complete_report(tmp_path)
    report["run"] = {"measurement": "behavioral-evaluation"}
    report["evidence"] = {"status": receipt}
    write_report(tmp_path, report)
    saved = json.loads((tmp_path / "report.json").read_text())
    assert saved["outcome"]["execution"]["status"] == "PASS"
    assert saved["outcome"]["behavior"]["status"] == expected
    assert saved["behavior"] == saved["run"]["behavior"] == expected
    assert saved["status"] == "ERROR"


def test_rejudging_does_not_inherit_the_original_behavioral_claim(tmp_path):
    report = complete_report(tmp_path)
    report["run"] = {"measurement": "behavioral-evaluation"}
    report["rejudged"] = {"execution": "PASS"}
    report["rejudge_evidence"] = {"status": "PASS"}
    write_report(tmp_path, report)
    saved = json.loads((tmp_path / "report.json").read_text())
    assert saved["outcome"]["execution"]["status"] == "PASS"
    assert saved["outcome"]["validity"]["status"] == "PASS"
    assert saved["behavior"] == "UNMEASURED"
    assert saved["rejudged"]["behavior"] == "UNMEASURED"
    assert saved["rejudged"]["measurement"] == "protocol-rehearsal"
    assert saved["run"] == {"measurement": "behavioral-evaluation"}


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
    """A row naming no candidate wake or session and carrying no closed
    canonical outcome stays unestablished — its execution axis is UNKNOWN
    and the report can never be a PASS."""
    report = complete_report(tmp_path)
    report["cases"][0]["wakes"][0].pop("session_id")
    report["cases"][0]["wakes"][0].pop("wake_id")
    frozen = json.loads((tmp_path / "wake-0-11.json").read_text())
    frozen["trajectory"]["session_id"] = None
    frozen["trajectory"]["wake_id"] = None
    (tmp_path / "wake-0-11.json").write_text(json.dumps(frozen))
    write_report(tmp_path, report)
    saved = json.loads((tmp_path / "report.json").read_text())
    assert saved["outcome"]["execution"]["status"] == "UNKNOWN"
    assert saved["outcome"]["execution"]["settled"] == 3
    assert saved["outcome"]["execution"]["unestablished"] == 1
    assert saved["outcome"]["validity"]["status"] == "PASS"
    assert saved["status"] == "ERROR"


def test_a_no_session_row_is_a_typed_silent_settlement(tmp_path):
    """A closed `cognition_wake_outcome` whose own session is NULL is the
    typed no-session settlement: the row keeps its real candidate wake,
    claims no session, and the axis is PASS on that receipt alone."""
    report = complete_report(tmp_path)
    wake = report["cases"][0]["wakes"][0]
    wake["session_id"] = None
    wake["no_session"] = "empty agenda"
    wake["trajectory"]["session_id"] = None
    wake["trajectory"]["effects"]["items"] = [
        _outcome_item("generated-unit", 9000, None, at_ms=1000)]
    frozen = json.loads((tmp_path / "wake-0-11.json").read_text())
    frozen["no_session"] = "empty agenda"
    frozen["trajectory"]["session_id"] = None
    (tmp_path / "wake-0-11.json").write_text(json.dumps(frozen))
    write_report(tmp_path, report)
    saved = json.loads((tmp_path / "report.json").read_text())
    assert saved["outcome"]["execution"]["status"] == "PASS"
    assert saved["outcome"]["execution"]["settled"] == 4


@pytest.mark.parametrize("damage,expected", [
    ("removed", "UNKNOWN"), ("foreign_unit", "ERROR"),
    ("foreign_wake", "UNKNOWN"), ("foreign_session", "ERROR"),
    ("failed", "ERROR"), ("unobserved", "UNKNOWN"),
    ("malformed", "UNKNOWN"), ("uncited", "UNKNOWN"),
    ("prose_only", "UNKNOWN"),
])
def test_a_wake_settles_only_on_its_own_closed_canonical_outcome(
        tmp_path, damage, expected):
    """Session existence is not settlement: removed, malformed or
    uncaptured effects stay UNKNOWN, a foreign or failed canonical
    outcome is ERROR — and a prose `no_session` detail alone never
    settles. Only the observed closed receipt counts."""
    report = complete_report(tmp_path)
    wake = report["cases"][0]["wakes"][0]
    effects = wake["trajectory"]["effects"]
    if damage == "removed":
        effects["items"] = []
    elif damage == "foreign_unit":
        effects["items"][0]["row"]["unit"] = "another-unit"
    elif damage == "foreign_wake":
        effects["items"][0]["row"]["wake_id"] = 4242
    elif damage == "foreign_session":
        effects["items"][0]["row"]["session_id"] = 9999
    elif damage == "failed":
        effects["items"][0]["row"]["outcome"] = "failed"
    elif damage == "unobserved":
        effects["status"] = "unavailable"
    elif damage == "malformed":
        effects["items"] = ["unreadable"]
    elif damage == "uncited":
        effects["items"][0]["ref"] = None
    elif damage == "prose_only":
        effects["items"] = []
        wake["session_id"] = None
        wake["no_session"] = "empty agenda"
        wake["trajectory"]["session_id"] = None
        frozen = json.loads((tmp_path / "wake-0-11.json").read_text())
        frozen["trajectory"]["session_id"] = None
        (tmp_path / "wake-0-11.json").write_text(json.dumps(frozen))
    write_report(tmp_path, report)
    saved = json.loads((tmp_path / "report.json").read_text())
    assert saved["coverage"]["status"] == "PASS"
    assert saved["outcome"]["execution"]["status"] == expected
    assert saved["outcome"]["execution"]["settled"] == 3
    assert saved["status"] == "ERROR"


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
                "session_id": None, "wake_id": None,
                "blocks": {"system": "generated", "agenda": "generated",
                           "items": [], "stimuli": [], "instructions": None}}
            wake["trajectory"] = trajectory
            frozen = json.loads(
                (tmp_path / f"wake-{index}-{wake['recorded_wake_id']}.json").read_text())
            frozen["trajectory"]["session_id"] = None
            frozen["trajectory"]["wake_id"] = None
            (tmp_path / f"wake-{index}-{wake['recorded_wake_id']}.json").write_text(
                json.dumps(frozen))
    report["cases"][0]["wakes"][1]["trajectory"]["blocks"] = {
        "system": "", "agenda": {}, "items": [], "stimuli": [],
        "instructions": None}
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
    frozen["trajectory"]["session_id"] = None
    frozen["trajectory"]["wake_id"] = None
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


def _frozen_turn(**overrides):
    """A minimal frozen turn artifact proving its own identity."""
    frozen = {
        "turn": 1, "unit": "generated-unit", "error": None,
        "declared_input": {"text": "the generated correction",
                           "event_id": "e-1", "at_ms": 2000},
        "trajectory": {"turn": 1, "unit": "generated-unit",
                       "session_id": None},
    }
    frozen.update(overrides)
    return frozen


def test_a_frozen_turn_proves_its_own_identity():
    from scripts.cognition_loop.accounting import frozen_turn_identity
    frozen = _frozen_turn()
    assert frozen_turn_identity(
        frozen, unit="generated-unit", turn=1,
        declared_input="the generated correction") is None


@pytest.mark.parametrize("damage,expect", [
    ("turn", "mismatch"), ("unit", "mismatch"),
    ("declared_input", "mismatch"), ("session_id", "mismatch"),
    ("no_session_key", "unknown"), ("no_trajectory", "unknown"),
])
def test_a_foreign_or_unproven_turn_artifact_is_named(damage, expect):
    from scripts.cognition_loop.accounting import frozen_turn_identity
    frozen = _frozen_turn()
    if damage == "turn":
        frozen["turn"] = 9
    elif damage == "unit":
        frozen["unit"] = "foreign-unit"
    elif damage == "declared_input":
        frozen["declared_input"] = {"text": "foreign text"}
    elif damage == "session_id":
        frozen["trajectory"]["session_id"] = "s-foreign"
    elif damage == "no_session_key":
        frozen["trajectory"].pop("session_id")
    else:
        frozen.pop("trajectory")
    verdict = frozen_turn_identity(
        frozen, unit="generated-unit", turn=1,
        declared_input="the generated correction")
    assert verdict is not None and expect in verdict


def _declared_report(output):
    """A declared-turn report and freeze bound to the original envelope."""
    declared = _recorded_input()
    (output / "turn-0.json").write_text(json.dumps({
        "turn": 0, "unit": "generated-unit", "error": None, "diff": {},
        "declared_envelope": _fields(), "declared_input": dict(declared),
        "trajectory": {"turn": 0, "unit": "generated-unit",
                       "session_id": None}}))
    return {
        "status": "PASS", "evidence": {"status": "PASS", "causes": []},
        "expected_cases": manifest([Case(
            name="generated-declared", unit="generated-unit", wakes=(),
            turns=(DeclaredTurn(envelope=_fields()),), version=2)]),
        "cases": [{"case": "generated-declared", "unit": "generated-unit",
                   "wakes": [], "turns": [{
                       "turn": 0, "at_ms": SENT, "session_id": None,
                       "declared_input": dict(declared),
                       "expectations": []}]}],
    }


def test_a_declared_turn_reconciles_against_the_original_declaration(tmp_path):
    coverage = reconcile(_declared_report(tmp_path), tmp_path)
    assert coverage["status"] == "PASS"
    assert coverage["expected"]["turns"] == 1


@pytest.mark.parametrize("damage", [
    "foreign_both", "foreign_frozen", "foreign_recorded",
    "missing_envelope", "foreign_envelope",
])
def test_a_declared_turns_identity_fails_against_the_declaration(
        tmp_path, damage):
    """A foreign actor, event or clock the frozen artifact and the report
    row share — with the same body text — is not identity: each answers to
    the original case declaration, never to each other."""
    report = _declared_report(tmp_path)
    frozen = json.loads((tmp_path / "turn-0.json").read_text())
    if damage == "foreign_both":
        foreign = _foreign_input()
        frozen["declared_input"] = dict(foreign)
        report["cases"][0]["turns"][0]["declared_input"] = dict(foreign)
    elif damage == "foreign_frozen":
        frozen["declared_input"] = _foreign_input()
    elif damage == "foreign_recorded":
        report["cases"][0]["turns"][0]["declared_input"] = _foreign_input()
    elif damage == "missing_envelope":
        frozen.pop("declared_envelope")
    else:
        frozen["declared_envelope"] = _foreign_fields()
    (tmp_path / "turn-0.json").write_text(json.dumps(frozen))
    coverage = reconcile(report, tmp_path)
    assert coverage["status"] == "ERROR"
    assert coverage["errors"]
