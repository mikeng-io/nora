"""The native SDK autonomous journey: generated input through the real App,
canonical evidence, both judges and the retained JSON/HTML report — one case."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

import pytest
from strands_evals import Case, Experiment

from evals import backbone
from evals.accounting import accepted, accounted, evidence_outcome
from evals.backbone import JourneyGrader, Spec, nora_task
from evals.rebuilt.entry import ENTRY_POINTS
from nora.subject.eval_evidence import EvidenceError, read_events
from nora.subject.obtain import SubjectSpec, build_config, default_policy
from nora.subject.recorded_services import RecordedServices
from nora.testing.causal_protocol import CORRECTION, INITIAL, TITLE
from nora.testing.journey_world import JourneyServices
from tests.integration.subject.test_sdk_recorded_caller import (
    sdk_evidence as sdk_evidence,
)

JUDGES = ("generated-a", "generated-b")

PLAN = {
    "steps": [
        {"send": INITIAL},
        {"until_wakes": 1},
        {"until_delivery": True},
        {"send": CORRECTION},
        {"until_wakes": 2},
    ],
    "expected_wakes": 2,
    "expectations": [
        {"wake": 0, "polarity": "must", "kind": "note",
         "text": "a note records the generated venue and time as undecided"},
        {"wake": 0, "polarity": "must", "kind": "reminder",
         "text": "a reminder asks which generated venue and time to select"},
        {"wake": 1, "polarity": "must", "kind": "note",
         "text": "a note records the generated venue Cedar Hall"},
    ],
    "judges": list(JUDGES),
}


def world(*, overrides=None):
    policy = (default_policy(("signal", "g1"), unit="generated-unit")
              + "home_room = true\nproactive = true\n")
    return JourneyServices(
        policy=policy, room=("signal", "g1"),
        overrides={
            "platforms.signal.mode": "bridge",
            "platforms.signal.enabled": True,
            "cognition.enabled": True,
            "heartbeat.enabled": True,
            # The finite plan is deterministic in two ways. A short fire
            # tick and a cue-quiet window wider than the reminder's whole
            # fire window keep the plan's sends the only wakes the world
            # produces; and every scheduled Dreamer adaptor's `wake_batch`
            # sits above the plan's message count, so the background tick
            # never holds the model slot the plan's own wakes and fires
            # need — the same not-due shape a quiet deployment shows.
            "heartbeat.tick_seconds": 5,
            "cognition.cue_quiet_seconds": 300,
            "culture.wake_batch": 1000,
            "memory.profile.person.update_every_messages": 1000,
            "memory.profile.unit.update_every_room_profile_changes": 1000,
            "memory.profile.nora.update_every_replies": 1000,
            **(overrides or {}),
        },
        judge_models=JUDGES)


def case():
    return Case(name="generated-autonomous-journey", input="",
                expected_output="", metadata={"journey": dict(PLAN)})


# The same generated world and the same production path, measured the
# new way: the claims anchor the produced turns, not the state kind a
# storage row landed in. `check` tokens are what the fixture itself
# made observable — finite rehearsal binding, never a quality claim.
PLAN_V2 = {
    "version": 2,
    "steps": list(PLAN["steps"]),
    "expected_wakes": 2,
    "expectations": [
        {"turn": 0, "polarity": "must", "kind": "behavior",
         "text": "the turn asks which generated venue and time to select",
         "check": "Which generated venue and time"},
        {"turn": 1, "polarity": "must", "kind": "behavior",
         "text": "the turn retains the generated correction — the venue "
                "record is revised to Cedar Hall",
         "check": "Cedar Hall"},
        {"turn": 1, "polarity": "must_not", "kind": "behavior",
         "text": "the turn does not assert the generated gathering "
                 "already happened",
         "check": "has taken place"},
    ],
    "judges": list(JUDGES),
}


def behavior_case():
    return Case(name="generated-behavior-journey", input="",
                expected_output="", metadata={"journey": dict(PLAN_V2)})


def test_autonomous_journey_entry_and_plan_preflight(evaluation_config, tmp_path):
    """Focused preflight: the declared entry routes the live-clock path and
    the generated world declares the same config the subject will compose."""
    port = ENTRY_POINTS["autonomous_journey"]
    assert port.live_clock and port.drive.__name__ == "autonomous_journey"
    plan = world()
    declared = build_config(SubjectSpec(
        database_url="sqlite://", workdir=tmp_path / "declared",
        policy=plan.policy, overrides=plan.overrides()))
    assert declared.cognition.enabled and declared.heartbeat.enabled
    assert declared.platforms.signal.enabled and declared.platforms.signal.mode == "bridge"
    spec = plan.subject_spec("sqlite://", None, workdir=tmp_path / "subject")
    assert spec.room == ("signal", "g1") and spec.environment is None
    assert spec.overrides["platforms.signal.mode"] == "bridge"
    assert spec.overrides["cognition.enabled"] is True


async def test_positional_recording_is_refused_as_a_journey_world(
    clean_pg_url, evaluation_config, monkeypatch, sdk_evidence,
):
    """Negative control: a `RecordedServices` plan — positional playback —
    can never stand in for the request-aware journey world."""
    sdk_evidence.record("expected_invalid_capture")
    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated-token")
    draw = case()
    recording = RecordedServices({"version": 1, "bindings": {"llm.base_url": "model"},
        "services": {"model": [{"method": "POST", "path": "/v1/chat/completions",
                                "response": {"status": 200, "json": {}}}]}})
    spec = Spec(source_url=clean_pg_url, entry="autonomous_journey",
                services={draw.name: recording})
    with pytest.raises(EvidenceError, match="journey world"):
        await backbone._drive(draw, spec)


def _durable_mirror(evidence: Any) -> Path:
    """Same-run evidence copied outside pytest's rotating basetemp."""
    root = Path(os.environ.get(
        "NORA_JOURNEY_ARTIFACTS",
        Path(tempfile.gettempdir()) / "nora-journey-artifacts"))
    dest = root / f"{evidence.path.name}-{uuid.uuid4().hex[:8]}"
    evidence.record("artifacts_mirrored", ref=str(dest))
    os.makedirs(root, mode=0o700, exist_ok=True)
    shutil.copytree(evidence.path, dest)
    os.chmod(dest, 0o700)
    return dest


async def test_sdk_autonomous_journey_through_real_app_to_judges_and_reports(
    clean_pg_url, evaluation_config, monkeypatch, sdk_evidence,
):
    """The whole path, once: SDK case -> App journey -> canonical evidence ->
    both judges -> retained JSON + HTML, with no forced wakes."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated-token")
    draw = case()
    plan = world()
    spec = Spec(source_url=clean_pg_url, entry="autonomous_journey",
                services={draw.name: plan})
    experiment = Experiment(cases=[draw], evaluators=[JourneyGrader()])
    report = await experiment.run_evaluations_async(
        task=nora_task(spec), max_workers=1)

    assert [rows[0].label for rows in report.detailed_results] == ["PASS"]
    events = read_events(sdk_evidence.path)
    coverage, outcome = accounted(
        report, experiment, evidence=evidence_outcome(sdk_evidence))
    assert accepted(coverage, outcome), (coverage, outcome)
    assert outcome["behavior"]["status"] == "UNMEASURED"
    # The same retention seam `python -m evals` uses: SDK rows plus the
    # three axes, durable in the case's evidence directory — the memory
    # copy is not the artifact.
    payload = backbone.retain_report(
        sdk_evidence, draw.name, report, experiment=experiment,
        coverage=coverage)
    native = json.loads(
        (sdk_evidence.path / f"corpus-{draw.name}.json").read_text())
    assert native["corpus"] == draw.name
    assert native["report"] == report.model_dump(mode="json")
    assert native["coverage"] == coverage
    assert native["outcome"]["execution"]["status"] == "PASS"
    assert native["outcome"]["validity"]["status"] == "PASS"
    assert native["outcome"]["behavior"]["status"] == "UNMEASURED"
    assert payload["outcome"]["behavior"]["status"] == "UNMEASURED"
    outputs = sdk_evidence.path / f"journey-{draw.name}"
    retained = json.loads((outputs / "report.json").read_text())
    assert retained["status"] == "PASS"
    assert retained["coverage"]["status"] == "PASS", retained["coverage"]["errors"]
    assert retained["outcome"]["execution"]["status"] == "PASS"
    assert retained["outcome"]["validity"]["status"] == "PASS"
    assert retained["outcome"]["behavior"]["status"] == "UNMEASURED"
    rendered = (outputs / "report.html").read_text()
    assert draw.name in rendered and "autonomous-live-journey" in rendered
    # The retained HTML renders the SAME report the JSON holds — its own
    # status, every axis, the coverage census and each wake's observed
    # lifecycle — never a page that merely names the case and mode.
    axes = retained["outcome"]
    assert (f'<span class="status-PASS">{retained["status"]}</span>'
            in rendered)
    for key, label in (("execution", "execution"),
                       ("validity", "measurement validity"),
                       ("behavior", "behaviour")):
        assert f"<th>{label}</th><td>{axes[key]['status']}</td>" in rendered
    census = retained["coverage"]["expected"]
    for name, count in census.items():
        assert f'&quot;{name}&quot;: {count}' in rendered
    first = retained["cases"][0]["wakes"][0]["trajectory"]
    assert (first["effects"]["status"] == "observed"
            and any(item["kind"] == "delivery"
                    for item in first["effects"]["items"]))
    assert "&quot;cognition_wake_outcome&quot;" in rendered
    assert "&quot;bridge_action&quot;" in rendered
    second = retained["cases"][0]["wakes"][1]["trajectory"]
    # The correction's own window opens at its send — after the delivery
    # the first wake's shared cut already captured — so the earlier wake
    # (with its delivery) is exactly the context its judges receive.
    assert second["effects"]["status"] == "observed"
    delivered_at = next(item["at"] for item in first["effects"]["items"]
                        if item["kind"] == "delivery")
    assert second["effects"]["since"] > delivered_at

    protocol, judge = plan.protocol, plan.judge
    assert protocol.phase == 9 and not protocol.errors
    assert len(protocol.requests) == 9 + protocol.background
    assert len(judge.requests) == 6 and not judge.errors
    assert {r["model"] for r in judge.requests} == set(JUDGES)
    captured = [json.loads(e["attributes"]["nora.model.request.body"])
                for e in events if e.get("name") == "nora.model.request"]
    assert captured == plan.requests

    case_rows = [c for c in retained["cases"] if c["case"] == draw.name]
    (journey,) = case_rows
    assert journey["status"] == "PASS" and len(journey["wakes"]) == 2
    assert all(w["session_id"] is not None for w in journey["wakes"])
    assert [e["verdict"] for w in journey["wakes"] for e in w["expectations"]] == [
        "PASS", "PASS", "PASS"]
    assert all(len(e["answers"]) == 2
               for w in journey["wakes"] for e in w["expectations"])
    assert all((outputs / f"wake-{i}-{w['recorded_wake_id']}.json").is_file()
               for i, w in enumerate(journey["wakes"]))

    # The case sandbox is dropped at teardown; the drive captured the same
    # rows into the retained report, and the claims check THAT record.
    receipts = retained["receipts"]
    wakes, outcomes = receipts["wakes"], receipts["outcomes"]
    assert len(wakes) == len(outcomes) == 2
    assert all(w["reason"] == "strong_cue" and w["mode"] == "live"
               and w["tracked"] == 1
               and not (w.get("detail") or {}).get("recorded_wake_forced")
               for w in wakes)
    assert all(o["outcome"] == "closed" for o in outcomes)
    assert len(receipts["sessions"]) == 2
    versions = receipts["note_revisions"]
    assert len(versions) == 2 and len({row["note_id"] for row in versions}) == 1
    assert "Cedar Hall" in str(versions)
    assert [row["title"] for row in receipts["notes"]] == [TITLE]
    actions = receipts["actions"]
    assert len(actions) == 1 and actions[0]["state"] == "confirmed"
    sends = [row for row in receipts["messages"] if row["is_nora"]]
    assert len(sends) == 1 and sends[0]["delivery_status"] == "sent"
    assert sends[0]["id"] == actions[0]["message_id"]
    reminders = receipts["reminders"]
    assert len(reminders) == 1 and reminders[0]["state"] == "done"
    sdk_evidence.record("protocol_assertions", status="PASS", execution="PASS",
                        validity="PASS", behavior="UNMEASURED")

    _durable_mirror(sdk_evidence)


async def test_sdk_autonomous_journey_version2_turns_through_real_app(
    clean_pg_url, evaluation_config, monkeypatch, sdk_evidence,
):
    """The version-2 measure end to end: the same generated world, the same
    real ingress and App, but the claims anchor the produced turns — each
    judged on its declared input, typed outcome and effects, both judges,
    with the store kinds present only as provenance."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated-token")
    draw = behavior_case()
    plan = world()
    spec = Spec(source_url=clean_pg_url, entry="autonomous_journey",
                services={draw.name: plan})
    experiment = Experiment(cases=[draw], evaluators=[JourneyGrader()])
    report = await experiment.run_evaluations_async(
        task=nora_task(spec), max_workers=1)

    assert [rows[0].label for rows in report.detailed_results] == ["PASS"]
    coverage, outcome = accounted(
        report, experiment, evidence=evidence_outcome(sdk_evidence))
    assert accepted(coverage, outcome), (coverage, outcome)
    payload = backbone.retain_report(
        sdk_evidence, draw.name, report, experiment=experiment,
        coverage=coverage)
    assert payload["outcome"]["behavior"]["status"] == "UNMEASURED"
    outputs = sdk_evidence.path / f"journey-{draw.name}"
    retained = json.loads((outputs / "report.json").read_text())
    assert retained["status"] == "PASS", retained["errors"]
    assert retained["version"] == 2
    assert retained["coverage"]["status"] == "PASS", retained["coverage"]["errors"]
    census = retained["coverage"]["expected"]
    assert census["turns"] == 2 and census["wakes"] == 2
    assert census["criteria"] == 3 and census["judge_answers"] == 6

    (journey,) = (c for c in retained["cases"] if c["case"] == draw.name)
    assert journey["status"] == "PASS"
    # The wake denominator is honestly retained — each send's natural wake
    # is a row — but the behavior claims live on the turn rows.
    assert len(journey["wakes"]) == 2 and len(journey["turns"]) == 2
    assert [t["outcome"] for t in journey["turns"]] == [
        "delivered", "intentional_non_delivery"]
    first, second = journey["turns"]
    assert first["trajectory"]["declared_input"]["text"] == INITIAL
    assert second["trajectory"]["declared_input"]["text"] == CORRECTION
    assert [e["verdict"] for t in journey["turns"]
            for e in t["expectations"]] == ["PASS", "PASS", "PASS"]
    assert all(len(e["answers"]) == 2
               for t in journey["turns"] for e in t["expectations"])
    for index in range(2):
        frozen = json.loads((outputs / f"turn-{index}.json").read_text())
        assert frozen["turn"] == index
        assert frozen["declared_input"]["text"] in (INITIAL, CORRECTION)
        assert frozen["trajectory"]["session_id"] is None
    # The state kinds stay provenance, never the oracle: the diff is
    # frozen beside the turn it is not allowed to decide.
    assert "diff" in frozen or "diff" in json.loads(
        (outputs / "turn-0.json").read_text())

    protocol, judge = plan.protocol, plan.judge
    assert protocol.phase == 9 and not protocol.errors
    assert len(judge.requests) == 6 and not judge.errors
    assert {r["model"] for r in judge.requests} == set(JUDGES)
    rendered = (outputs / "report.html").read_text()
    assert draw.name in rendered and "turn" in rendered
    sdk_evidence.record("protocol_assertions", status="PASS",
                        version=2, behavior="UNMEASURED")

    _durable_mirror(sdk_evidence)
