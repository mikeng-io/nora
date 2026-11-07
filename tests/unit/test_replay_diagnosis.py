"""Generated controls for the decision diagnostic; no model or replay."""

from __future__ import annotations

import pytest

from nora.subject.replay_diagnosis import diagnose


def scripted_judge(statement, events):
    """The wire shape varies with the record, including invalid responses."""
    if statement.get("wrong_shape"):
        return [{"verdict": "wrong protocol"}]
    reason = next(e for e in events if e["kind"] == "transport_response")
    text = reason["reasoning"]
    if "not memory" in text:
        return {
            "observation": "rejected_as_not_memory",
            "event_ids": [reason["event_id"]],
            "reason": text,
            "quote": "not memory",
        }
    if "uncited" in text:
        return {
            "observation": "uncited_detail",
            "event_ids": [reason["event_id"]],
            "reason": "detail from uncited row",
        }
    terminal = [e for e in events if e.get("name") == "nora.session.terminal"]
    if terminal:
        return {
            "observation": "named_bound",
            "event_ids": [terminal[0]["event_id"]],
            "reason": "session reached token bound",
        }
    proposals = [e for e in events if e["kind"] == "tool_finished"]
    if proposals:
        return {
            "observation": "refused_not_retried",
            "event_ids": [proposals[0]["event_id"]],
            "reason": "proposal refused",
        }
    return {
        "observation": "not_mentioned",
        "event_ids": [reason["event_id"]],
        "reason": "not present in emitted reasoning",
    }


@pytest.mark.parametrize(
    ("control", "expected"),
    [
        ("missing", "insufficient_evidence"),
        ("redacted", "insufficient_evidence"),
        ("unavailable", "insufficient_evidence"),
        ("rejection", "rejected_as_not_memory"),
        ("refusal", "refused_not_retried"),
        ("retry", "insufficient_evidence"),
        ("budget", "named_bound"),
        ("uncited", "uncited_detail"),
        ("unmentioned", "not_mentioned"),
        ("wrong_shape", "insufficient_evidence"),
    ],
)
def test_decision_controls(control, expected):
    events = [
        {
            "kind": "transport_finished",
            "event_id": "e1",
            "reasoning_availability": {
                "missing": "absent",
                "redacted": "redacted",
                "unavailable": "capture_unavailable",
            }.get(control, "observed"),
        },
        {
            "kind": "transport_response",
            "event_id": "e2",
            "reasoning": {"rejection": "not memory", "uncited": "took uncited detail"}.get(
                control, "other"
            ),
        },
    ]
    if control in {"refusal", "retry"}:
        events.append(
            {
                "kind": "tool_finished",
                "event_id": "e3",
                "tool": {"name": "memory_propose"},
                "result": {"status": "refused"},
            }
        )
    if control == "retry":
        events.append(
            {
                "kind": "tool_finished",
                "event_id": "e4",
                "tool": {"name": "memory_propose"},
                "result": {"status": "saved"},
            }
        )
    if control == "budget":
        events.append(
            {
                "kind": "event",
                "name": "nora.session.terminal",
                "event_id": "e3",
                "attributes": {"body": '{"bound":"tokens"}'},
            }
        )
    statement = {
        "kind": "unsupported" if control == "uncited" else "missed",
        "wrong_shape": control == "wrong_shape",
    }
    assert diagnose(statement, events, scripted_judge(statement, events))["observation"] == expected


def test_unknown_event_cannot_ground_a_diagnostic():
    events = [
        {"kind": "transport_finished", "event_id": "e1", "reasoning_availability": "observed"}
    ]
    assert (
        diagnose(
            {"kind": "missed"},
            events,
            {"observation": "not_mentioned", "event_ids": ["nonexistent"], "reason": "claim"},
        )["observation"]
        == "insufficient_evidence"
    )
