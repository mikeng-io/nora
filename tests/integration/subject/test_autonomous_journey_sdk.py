"""Autonomous SDK entry and service-boundary controls."""

from __future__ import annotations

import pytest
from strands_evals import Case

from evals import backbone
from evals.backbone import Spec
from evals.rebuilt.entry import ENTRY_POINTS
from nora.subject.eval_evidence import EvidenceError
from nora.subject.journey_world import JourneyServices
from nora.subject.obtain import _DEFAULT_IDENTITY, SubjectSpec, build_config, default_policy
from nora.subject.recorded_services import RecordedServices
from nora.testing.causal_protocol import CAUSAL_TIMING, CORRECTION, INITIAL
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
              .replace('persons = "*"', 'persons = ["miles_novak"]')
              + "home_room = true\nproactive = true\n")
    return JourneyServices(
        policy=policy, room=("signal", "g1"), identity=_DEFAULT_IDENTITY,
        overrides={
            "platforms.signal.mode": "bridge",
            "platforms.signal.enabled": True,
            "heartbeat.enabled": True,
            **CAUSAL_TIMING,
            **(overrides or {}),
        },
        judge_models=JUDGES)


def case():
    return Case(name="generated-autonomous-journey", input="",
                expected_output="", metadata={"journey": dict(PLAN)})


def test_autonomous_journey_entry_and_plan_preflight(evaluation_config, tmp_path):
    """Focused preflight: the declared entry routes the live-clock path and
    the generated world declares the same config the subject will compose."""
    port = ENTRY_POINTS["autonomous_journey"]
    assert port.live_clock and port.drive.__name__ == "autonomous_journey"
    plan = world()
    declared = build_config(SubjectSpec(
        database_url="sqlite://", workdir=tmp_path / "declared",
        policy=plan.policy, overrides=plan.overrides()))
    assert declared.heartbeat.enabled
    assert declared.platforms.signal.enabled and declared.platforms.signal.mode == "bridge"
    spec = plan.subject_spec("sqlite://", None, workdir=tmp_path / "subject")
    assert spec.room == ("signal", "g1") and spec.environment is None
    assert spec.overrides["platforms.signal.mode"] == "bridge"


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
