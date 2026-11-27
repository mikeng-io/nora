"""Declared-envelope journey inputs: parse controls, plan validation and
frozen-identity damage — pure controls, no App, no store."""

import pytest

from evals.cognition_loop.accounting import frozen_turn_identity
from evals.cognition_loop.judges import turn_gap
from evals.rebuilt.autonomous_journey import _expected_turns, _input_ctx
from evals.rebuilt.journey_manifest import _plan_error
from evals.rebuilt.journey_turns import _declared_input, _turn_causes
from nora.domain.models import NormalizedMessage
from nora.platform.bridge.envelope import Envelope, canonical_source_key
from nora.platform.bridge.rehydrate import message_to_dict
from nora.subject.journey import declared_envelope, declared_source

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


def test_input_ctx_binds_the_declared_declaration_and_instant():
    ctx = _input_ctx({"envelope": _fields()}, sent=1, engine=None,
                     boundary=None, cursor=None)
    assert ctx["at_ms"] == SENT
    assert ctx["input"] == "the declared body"
    assert ctx["envelope"].sender_id == ACTOR
    assert ctx["source"]["actor"] == ACTOR
    assert ctx["source"]["sent_ms"] == SENT


def test_declared_input_carries_source_only_for_declared_envelopes():
    ctx = {"input": "the declared body", "event_id": "v1:declared-42",
           "at_ms": SENT, "source": {"actor": ACTOR}}
    assert _declared_input(ctx).get("source") == {"actor": ACTOR}
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
    return {"effects": {"status": "observed", "items": [], "tools": []},
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


def _declared_frozen(fields=None, declared=None):
    """The frozen turn artifact a declared input leaves."""
    return {"turn": 0, "unit": "generated-unit",
            "declared_envelope": _fields() if fields is None else fields,
            "declared_input": (_recorded_input() if declared is None
                               else declared),
            "trajectory": {"session_id": None, "unit": "generated-unit"}}


def _foreign_fields(*, actor=False, clock=False):
    """A foreign but still-valid declaration's wire fields, same body text."""
    fields = _fields(event_id="v1:foreign-99")
    message = dict(fields["message"])
    if actor:
        fields["sender_id"] = "+85290000111"
        fields["sender_name"] = "Rex"
        message["sender_id"] = "+85290000111"
        message["sender_name"] = "Rex"
    if clock:
        fields["sent_ms"] = SENT + 1
        message["timestamp"] = SENT + 1
    fields["message"] = message
    return fields


def _foreign_input(**overrides):
    foreign = declared_envelope(_foreign_fields(**overrides))
    return {"text": "the declared body",
            "event_id": foreign.event_id,
            "at_ms": int(foreign.sent_ms),
            "source": declared_source(foreign)}


def test_a_declared_turns_identity_answers_to_the_original_declaration():
    declared = _recorded_input()
    assert frozen_turn_identity(
        _declared_frozen(), unit="generated-unit", turn=0,
        declared_input={"envelope": _fields()},
        recorded_input=dict(declared)) is None


@pytest.mark.parametrize("damage", ["actor", "event", "clock"])
def test_a_declared_turn_rejects_a_foreign_identity_in_both_artifacts(damage):
    """The row and the frozen artifact agreeing on a foreign actor, event
    or clock — with the same body text — cannot stand in for the original
    declaration: agreement between two derived artifacts is not identity."""
    foreign = _foreign_input(
        actor=(damage == "actor"), clock=(damage == "clock"))
    frozen = _declared_frozen(fields=_foreign_fields(
        actor=(damage == "actor"), clock=(damage == "clock")),
        declared=foreign)
    cause = frozen_turn_identity(
        frozen, unit="generated-unit", turn=0,
        declared_input={"envelope": _fields()},
        recorded_input=dict(foreign))
    assert cause is not None


def test_a_declared_turn_rejects_a_projection_the_envelope_disproves():
    """The frozen envelope carries the original declaration but the
    artifacts' shared projection drifted: the projection answers to the
    declaration, not to the artifacts' agreement."""
    foreign = _foreign_input()
    frozen = _declared_frozen(declared=foreign)
    cause = frozen_turn_identity(
        frozen, unit="generated-unit", turn=0,
        declared_input={"envelope": _fields()},
        recorded_input=dict(foreign))
    assert cause is not None
    assert "declared_input" in cause


def test_a_declared_turn_needs_its_frozen_envelope():
    frozen = _declared_frozen()
    frozen.pop("declared_envelope")
    cause = frozen_turn_identity(
        frozen, unit="generated-unit", turn=0,
        declared_input={"envelope": _fields()},
        recorded_input=_recorded_input())
    assert "declared_envelope" in cause


def test_a_declared_turn_needs_a_legible_declaration():
    cause = frozen_turn_identity(
        _declared_frozen(), unit="generated-unit", turn=0,
        declared_input={"envelope": {"v": 99}},
        recorded_input=_recorded_input())
    assert "unknown" in cause and "declared_input" in cause


def test_a_declared_turn_rejects_a_foreign_report_projection():
    cause = frozen_turn_identity(
        _declared_frozen(), unit="generated-unit", turn=0,
        declared_input={"envelope": _fields()},
        recorded_input=_foreign_input())
    assert "recorded_input" in cause


def test_a_declared_turn_needs_the_report_projection():
    cause = frozen_turn_identity(
        _declared_frozen(), unit="generated-unit", turn=0,
        declared_input={"envelope": _fields()}, recorded_input=None)
    assert "recorded_input" in cause


def test_the_manifest_keeps_the_original_declaration():
    fields = _fields()
    turns = _expected_turns(
        [{"send": "generated"}, {"envelope": fields}, {"skip": True}])
    assert turns == ["generated", {"envelope": fields}]
    declared = declared_envelope(turns[1]["envelope"])
    assert declared_source(declared)["actor"] == ACTOR
    assert declared_source(declared)["sent_ms"] == SENT


def _declared_ctx(event):
    declared = declared_envelope(_fields())
    return {"index": 0, "input": "the declared body",
            "event_id": "v1:declared-42", "at_ms": SENT,
            "envelope": declared, "source": declared_source(declared),
            "event": event,
            "work": [{"kind": "ingest", "state": "done"}]}


def test_the_matching_admitted_payload_clears_the_turn():
    ctx = _declared_ctx({"id": "v1:declared-42", "state": "done",
                         "payload": {"envelope": _fields()}})
    assert _turn_causes(ctx, _trajectory(_recorded_input()), ()) == []


@pytest.mark.parametrize("payload,cause", [
    (None, "admission_declared_missing"),
    ({}, "admission_declared_missing"),
    ({"envelope": "the declared body"}, "admission_declared_missing"),
    ({"envelope": {"v": 99}}, "admission_declared_foreign"),
])
def test_a_missing_or_unreadable_admitted_payload_is_named(payload, cause):
    ctx = _declared_ctx({"id": "v1:declared-42", "state": "done",
                         "payload": payload})
    assert cause in _turn_causes(ctx, _trajectory(_recorded_input()), ())


def test_a_foreign_admitted_payload_is_named():
    ctx = _declared_ctx({"id": "v1:declared-42", "state": "done",
                         "payload": {"envelope": _foreign_fields()}})
    causes = _turn_causes(ctx, _trajectory(_recorded_input()), ())
    assert "admission_declared_foreign" in causes


def test_a_foreign_admitted_body_is_named():
    fields = _fields()
    fields["message"] = {**fields["message"],
                         "body": "a different declared body"}
    ctx = _declared_ctx({"id": "v1:declared-42", "state": "done",
                         "payload": {"envelope": fields}})
    causes = _turn_causes(ctx, _trajectory(_recorded_input()), ())
    assert "admission_declared_foreign" in causes


@pytest.mark.parametrize("message_fields", [
    {"quote": {"quoted_ts": SENT - 1000, "author_id": ACTOR,
               "text": "a substituted quoted message"}},
    {"media": [{"platform_id": "generated-image", "media_type": "image/jpeg"}]},
])
def test_same_body_cannot_hide_a_substituted_quote_or_media(message_fields):
    fields = _fields()
    fields["message"] = {**fields["message"], **message_fields}
    ctx = _declared_ctx({"id": "v1:declared-42", "state": "done",
                         "payload": {"envelope": fields}})
    assert declared_source(declared_envelope(fields)) == ctx["source"]
    causes = _turn_causes(ctx, _trajectory(_recorded_input()), ())
    assert "admission_declared_foreign" in causes
