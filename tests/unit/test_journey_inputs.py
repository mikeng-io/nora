"""Declared-envelope journey inputs: parse controls, plan validation and
frozen-identity damage — pure controls, no App, no store."""

import pytest

from evals.rebuilt.journey_manifest import _plan_error
from evals.rebuilt.journey_turns import _declared_input, declared_source
from nora.domain.models import NormalizedMessage
from nora.platform.bridge.envelope import Envelope, canonical_source_key
from nora.platform.bridge.rehydrate import message_to_dict
from nora.subject.journey import declared_envelope
from scripts.cognition_loop.accounting import frozen_turn_identity
from scripts.cognition_loop.judges import turn_gap

ACTOR, HANDLE = "+85290000110", "Ada"
SENT, RECEIVED, QUEUED = 1_600_000_000_000, 1_600_000_000_050, 1_600_000_000_090


def _fields(**overrides):
    """A valid declared v2 envelope's wire fields, generated."""
    source = canonical_source_key(
        "signal", "declared-acct", "g1", "message", "upstream-42")
    message = NormalizedMessage(
        "signal", "g1", "group", ACTOR, HANDLE, SENT,
        "the declared body",
        {"dataMessage": {"message": "the declared body"}},
        message_id="upstream-42", mentions=frozenset({"generated-self"}))
    fields = Envelope(
        platform="signal", account_key="declared-acct", kind="message",
        source_key=source, event_id="v1:declared-42", conversation_id="g1",
        conversation_type="group", sender_id=ACTOR, sender_name=HANDLE,
        sent_ms=SENT, received_ms=RECEIVED, queued_ms=QUEUED,
        time_quality="upstream", message=message_to_dict(message),
        admission_rev=1).to_fields()
    fields.update(overrides)
    return fields


def _v2_plan(step):
    return {"version": 2, "expected_wakes": 0, "steps": [step],
            "expectations": [], "judges": ["judge-a", "judge-b"]}


def _frozen(declared_input):
    return {"turn": 0, "unit": "generated-unit",
            "declared_input": declared_input,
            "trajectory": {"session_id": None, "unit": "generated-unit"}}


def test_declared_envelope_preserves_actor_identity_and_clock():
    declared = declared_envelope(_fields())
    assert (declared.platform, declared.account_key) == ("signal", "declared-acct")
    assert declared.conversation_id == "g1"
    assert declared.sender_id == ACTOR and declared.sender_name == HANDLE
    assert declared.event_id == "v1:declared-42"
    assert declared.source_key.endswith("upstream-42")
    assert (declared.sent_ms, declared.received_ms, declared.queued_ms) == (
        SENT, RECEIVED, QUEUED)
    assert declared.time_quality == "upstream"


def test_declared_envelope_rejects_a_non_object():
    with pytest.raises(ValueError, match="not an envelope object"):
        declared_envelope("the declared body")


def test_declared_envelope_rejects_unknown_fields():
    with pytest.raises(ValueError, match="unknown fields"):
        declared_envelope(_fields(**{"generated": True}))


def test_declared_envelope_rejects_an_invalid_wire_shape():
    with pytest.raises(ValueError, match="not a valid production envelope"):
        declared_envelope(_fields(v=99))


def test_declared_envelope_rejects_a_contradictory_source_key():
    foreign = canonical_source_key(
        "signal", "declared-acct", "other-room", "message", "upstream-42")
    with pytest.raises(ValueError, match="source key contradicts"):
        declared_envelope(_fields(source_key=foreign))


def test_declared_envelope_rejects_a_message_free_declaration():
    fields = _fields(message=None)
    fields["v"] = 2
    fields["observation"] = {"kind": "reaction", "data": {}}
    with pytest.raises(ValueError, match="carries no message"):
        declared_envelope(fields)


def test_declared_envelope_rejects_a_missing_message_body():
    fields = _fields()
    fields["message"] = {**fields["message"], "body": "  "}
    with pytest.raises(ValueError, match="body is empty"):
        declared_envelope(fields)


def test_declared_envelope_rejects_contradictory_outer_identity():
    fields = _fields()
    fields["message"] = {**fields["message"], "sender_id": "other-actor"}
    with pytest.raises(ValueError, match="identity disagree") as caught:
        declared_envelope(fields)
    assert "other-actor" not in str(caught.value)
    fields = _fields()
    fields["message"] = {**fields["message"], "timestamp": SENT + 1}
    with pytest.raises(ValueError, match="identity disagree"):
        declared_envelope(fields)


def test_plan_accepts_a_declared_v2_envelope_step():
    assert _plan_error(_v2_plan({"envelope": _fields()})) is None


def test_plan_refuses_a_step_naming_both_input_alternatives():
    for step in ({"envelope": _fields(), "send": "generated"},
                 {"envelope": _fields(), "tagged": True}):
        assert _plan_error(_v2_plan(step)) == (
            "a journey input step names both a declared envelope and "
            "another input alternative")


def test_plan_refuses_a_declared_step_on_version_1():
    plan = _v2_plan({"envelope": _fields()})
    plan["version"] = 1
    plan["expected_wakes"] = 1
    plan["steps"].append({"until_wakes": 1})
    assert _plan_error(plan) == (
        "a declared envelope step belongs to version-2 journey inputs")


def test_plan_refuses_an_invalid_declaration_with_a_fixed_cause():
    fields = _fields(source_key="not-canonical")
    cause = _plan_error(_v2_plan({"envelope": fields}))
    assert cause is not None and "not-canonical" not in cause


def test_declared_source_projects_actor_source_and_clock():
    source = declared_source(declared_envelope(_fields()))
    assert source["actor"] == ACTOR and source["handle"] == HANDLE
    assert source["event_id"] == "v1:declared-42"
    assert source["conversation_id"] == "g1"
    assert source["sent_ms"] == SENT and source["received_ms"] == RECEIVED
    assert source["time_quality"] == "upstream"
    assert source["message_id"] == "upstream-42"
    assert source["mention_names"] == ["generated-self"]


def test_declared_input_carries_source_only_for_declared_envelopes():
    ctx = {"input": "the declared body", "event_id": "v1:declared-42",
           "at_ms": SENT, "source": {"actor": ACTOR}}
    assert _declared_input(ctx)["source"] == {"actor": ACTOR}
    generated = {"input": "generated", "event_id": "g", "at_ms": 1,
                 "source": None}
    assert "source" not in _declared_input(generated)


def _recorded_input(**source_overrides):
    source = declared_source(declared_envelope(_fields()))
    source.update(source_overrides)
    return {"text": "the declared body", "event_id": "v1:declared-42",
            "at_ms": SENT, "source": source}


def test_frozen_turn_identity_accepts_the_same_declared_input():
    declared = _recorded_input()
    frozen = _frozen(dict(declared))
    assert frozen_turn_identity(
        frozen, unit="generated-unit", turn=0,
        declared_input="the declared body",
        recorded_input=dict(declared)) is None


@pytest.mark.parametrize("key,value", [
    ("actor", "other-actor"), ("event_id", "v1:other"),
    ("conversation_id", "other-room"), ("sent_ms", SENT + 1),
])
def test_frozen_turn_identity_rejects_a_foreign_declared_input(key, value):
    frozen = _frozen(_recorded_input())
    recorded = _recorded_input(**{key: value})
    cause = frozen_turn_identity(
        frozen, unit="generated-unit", turn=0,
        declared_input="the declared body", recorded_input=recorded)
    assert cause is not None and "declared_input" in cause


def test_frozen_turn_identity_rejects_source_the_row_never_recorded():
    frozen = _frozen(_recorded_input())
    cause = frozen_turn_identity(
        frozen, unit="generated-unit", turn=0,
        declared_input="the declared body", recorded_input=None)
    assert cause is not None and "declared_input" in cause


def test_frozen_turn_identity_keeps_generated_inputs_unchanged():
    generated = {"text": "generated", "event_id": "g", "at_ms": 1}
    frozen = _frozen(dict(generated))
    assert frozen_turn_identity(
        frozen, unit="generated-unit", turn=0,
        declared_input="generated", recorded_input=dict(generated)) is None


def _trajectory(declared_input):
    return {"effects": {"status": "observed"},
            "declared_input": declared_input, "session_id": None,
            "bound": {"event_id": declared_input["event_id"],
                      "session_id": None},
            "turn_outcome": {"status": "recorded"},
            "settlement": {"status": "observed"}}


def test_turn_gap_accepts_a_complete_declared_source():
    assert "declared_input_unbound" not in turn_gap(
        _trajectory(_recorded_input()))


@pytest.mark.parametrize("key", ["actor", "event_id", "conversation_id",
                                 "sent_ms"])
def test_turn_gap_rejects_an_unbound_declared_source(key):
    declared = _recorded_input()
    declared["source"][key] = None
    assert "declared_input_unbound" in turn_gap(_trajectory(declared))


def test_turn_gap_keeps_generated_inputs_unchanged():
    declared = {"text": "generated", "event_id": "g", "at_ms": 1}
    assert "declared_input_unbound" not in turn_gap(_trajectory(declared))
