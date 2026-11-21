"""Decide wake admission from graph stimuli, claim history and time."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from nora.domain.graph_stimuli import GraphStimulus
from nora.memory.domain.wake import WakeDecision, WakeHistory, WakePolicy, decide_wake

NOW = datetime(2026, 11, 20, 12, tzinfo=UTC)
POLICY = WakePolicy(
    quiet_seconds=60, cue_quiet_seconds=5, batch_max=3, max_age_seconds=120,
    cooldown_seconds=10, max_concurrent_sessions=2, backoff_max_seconds=15,
)


def signals(*kinds_and_ages):
    return tuple(
        GraphStimulus("unit", kind, f"item-{index}", NOW - timedelta(seconds=age), "fixture")
        for index, (kind, age) in enumerate(kinds_and_ages)
    )


@pytest.mark.parametrize(("reason", "stimuli"), [
    ("settled", (("new_item", 61),)),
    ("strong_cue", (("strong_cue", 6),)),
    ("batch", (("new_item", 0), ("verdict", 0), ("dependency", 0))),
    ("max_age", (("new_item", 121), ("new_item", 0))),
    ("clock", (("review_time", 0),)),
    ("clock", (("valid_to", 0),)),
    ("clock", (("deadline", 0),)),
])
def test_each_wake_reason_decides_alone(reason, stimuli):
    assert decide_wake(signals(*stimuli), WakeHistory(), NOW, POLICY,
                       attempts=0) == WakeDecision(reason=reason)


@pytest.mark.parametrize(("brake", "history", "stimuli", "attempts"), [
    ("empty", WakeHistory(), (), 0),
    ("single_flight", WakeHistory(unit_in_flight=True), (("deadline", 0),), 0),
    ("concurrency", WakeHistory(in_flight=2), (("deadline", 0),), 0),
    ("cooldown", WakeHistory(latest_settled=NOW), (("deadline", 0),), 0),
    ("backoff", WakeHistory(latest_settled=NOW - timedelta(seconds=14)), (("deadline", 0),), 2),
])
def test_each_wake_brake_decides_alone(brake, history, stimuli, attempts):
    assert decide_wake(signals(*stimuli), history, NOW, POLICY,
                       attempts=attempts) == WakeDecision(brake=brake)


def test_failure_backoff_is_capped():
    history = WakeHistory(latest_settled=NOW - timedelta(seconds=15))
    assert decide_wake(signals(("deadline", 0)), history, NOW, POLICY,
                       attempts=2) == WakeDecision(reason="clock")


def test_a_strong_cue_cuts_cooldown_once_after_a_closed_wake():
    closed = NOW - timedelta(seconds=2)
    history = WakeHistory(latest_claim=closed, latest_closed_claim=closed, latest_settled=closed)
    cue = signals(("strong_cue", 6))
    assert decide_wake(cue, history, NOW, POLICY, attempts=0) == WakeDecision(reason="strong_cue")
    failed = replace(history, latest_claim=NOW, latest_settled=NOW)
    assert decide_wake(cue, failed, NOW, POLICY, attempts=0) == WakeDecision(brake="cooldown")


def test_a_failed_wake_starts_cooldown():
    history = WakeHistory(latest_claim=NOW, latest_settled=NOW)
    assert decide_wake(signals(("deadline", 0)), history, NOW, POLICY,
                       attempts=1) == WakeDecision(brake="cooldown")


def test_verdict_and_dependency_wait_for_ordinary_reasons():
    for kind in ("verdict", "dependency"):
        assert decide_wake(signals((kind, 0)), WakeHistory(), NOW, POLICY,
                           attempts=0) == WakeDecision(brake="waiting")
        assert decide_wake(signals((kind, 61)), WakeHistory(), NOW, POLICY,
                           attempts=0) == WakeDecision(reason="settled")
