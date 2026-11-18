"""The generated journey world's model dispatch, at the protocol level.

`CausalProtocol` serves the production occasions this world can raise —
dream-model scenario turns, tag-lane turns, the unit and room folds, and the
Dreamer's adaptor passes — dispatched by each request's own characteristics
(model lane, `<dreamer_objective>` tag, offered tool schemas, fold scope),
and names every unmatched request into `errors`. `JourneyServices` hands a
request to the judge aliases by declared model, never by the `stream` flag.
"""

import json

import pytest

from nora.subject.journey_world import GeneratedJudge, JourneyServices, make_causal_protocol
from nora.subject.obtain import SubjectSpec, build_config, default_policy
from nora.testing.causal_protocol import (
    ANSWER,
    CORRECTION,
    FOLLOWUP,
    INITIAL,
    QUESTION,
    TITLE,
)

_POLICY = (
    default_policy(("signal", "g1"), unit="generated-unit") + "home_room = true\nproactive = true\n"
)

_OVERRIDES = {
    "platforms.signal.mode": "bridge",
    "platforms.signal.enabled": True,
    "cognition.enabled": True,
    "heartbeat.enabled": True,
}

#: Offered specs production's own tool calls would validate against — the
#: protocol binds its generated arguments to the request's OFFERED schema,
#: so a spec that rejects them must fail the call, not be ignored.
_NOTE_CREATE = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "uncertainty": {"type": "string"},
        "closing_condition": {"type": "string"},
        "body": {"type": "string"},
        "anchor_entities": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "body"],
}
_ANY_ARGS = {"type": "object"}


def _tool(name, parameters=_ANY_ARGS):
    return {"type": "function", "function": {"name": name, "parameters": parameters}}


def _body(model, content, *, tools=(), stream=True, role="user"):
    return _req(model, [{"role": role, "content": content}], tools=tools, stream=stream)


def _req(model, messages, *, tools=(), stream=True):
    body = {"model": model, "stream": stream, "messages": messages}
    if tools:
        body["tools"] = [
            t if isinstance(t, dict) else _tool(*t) if isinstance(t, tuple) else _tool(t)
            for t in tools
        ]
    return body


def _content(response):
    return response["chunks"][0]["choices"][0]["delta"]["content"]


def _call(response):
    call = response["chunks"][0]["choices"][0]["delta"]["tool_calls"][0]
    return (call["function"]["name"], json.loads(call["function"]["arguments"]))


@pytest.fixture
def cfg(tmp_path, evaluation_config):
    return build_config(
        SubjectSpec(
            database_url="sqlite://",
            workdir=tmp_path / "declared",
            policy=_POLICY,
            overrides=_OVERRIDES,
        )
    )


@pytest.fixture
def protocol(cfg):
    return make_causal_protocol(cfg)


def _serve(protocol, body):
    status, response = protocol.dispatch(body)
    assert status == 200, response
    return response


def _drive_first_turn(protocol):
    """Write the note, arm the reminder, then close the tool-less cycle."""
    assert (
        _call(
            _serve(
                protocol,
                _body(
                    protocol.cfg.dreamer.model,
                    f'<m n="17">{INITIAL}</m>',
                    tools=(_tool("note_create", _NOTE_CREATE), "remind"),
                ),
            )
        )[0]
        == "note_create"
    )
    assert (
        _call(
            _serve(
                protocol,
                _body(
                    protocol.cfg.dreamer.model,
                    '{"status":"ok","note_id":"n1"}',
                    tools=("remind",),
                    role="tool",
                ),
            )
        )[0]
        == "remind"
    )
    assert "retained" in _content(
        _serve(
            protocol,
            _body(protocol.cfg.dreamer.model, '{"status":"ok","reminder_id":"r1"}', role="tool"),
        )
    )


def _drive(protocol):
    """The whole V1 plan through phase 10, asserting each phase's own answer."""
    _drive_first_turn(protocol)
    fold = _serve(
        protocol,
        _body(protocol.cfg.llm.model, "Fold the unit. Scope of this call: UNIT", role="system"),
    )
    assert json.loads(_content(fold)) == {"items": []}
    name, args = _call(
        _serve(
            protocol,
            _body(protocol.cfg.llm.model, f"the reminder asks: {QUESTION}", tools=("send_reply",)),
        )
    )
    assert (name, args["text"]) == ("send_reply", QUESTION)
    assert (
        _call(
            _serve(
                protocol,
                _body(
                    protocol.cfg.dreamer.model,
                    f"n1 {CORRECTION}",
                    tools=("note_revise", "note_read"),
                ),
            )
        )[0]
        == "note_read"
    )
    name, args = _call(
        _serve(
            protocol,
            _req(
                protocol.cfg.dreamer.model,
                [
                    {
                        "role": "tool",
                        "content": '{"status":"ok","revision_id":"rev1","body":"old",'
                        f'"title":"{TITLE}"}}',
                    },
                    {"role": "user", "content": f'<m n="42">{CORRECTION}</m>'},
                ],
                tools=("note_revise",),
            ),
        )
    )
    assert name == "note_revise" and args["note_id"] == "n1"
    assert "retained" in _content(
        _serve(
            protocol,
            _body(protocol.cfg.dreamer.model, '{"status":"ok","revision_id":"rev2"}', role="tool"),
        )
    )
    fold = _serve(
        protocol,
        _body(protocol.cfg.llm.model, "Fold the unit. Scope of this call: UNIT", role="system"),
    )
    assert json.loads(_content(fold)) == {"items": []}
    name, args = _call(
        _serve(
            protocol,
            _body(protocol.cfg.llm.model, f"{FOLLOWUP} — {CORRECTION}", tools=("send_reply",)),
        )
    )
    assert (name, args["text"]) == ("send_reply", ANSWER)
    fold = _serve(
        protocol,
        _body(protocol.cfg.llm.model, "<standing_prior>prev</standing_prior>", role="system"),
    )
    assert json.loads(_content(fold))["standing"]


def test_every_scenario_phase_gets_its_own_production_contract(protocol):
    """The 11-call plan answers tool calls against the offered schemas,
    text on the close calls, `items` on the unit folds and `standing` on
    the room fold — no phase borrows another phase's answer."""
    _drive(protocol)
    assert protocol.phase == 11 and not protocol.errors
    assert len(protocol.requests) == 11
    assert protocol.occasions == {"cognition": 6, "turn": 2, "fold_unit": 2, "fold_room": 1}


def test_an_interleaved_background_pass_cannot_consume_scenario_progress(protocol):
    """A culture pass and a character-session pass between dream-model turns
    are answered as background — `phase` stays put, and the next scenario
    call still meets the shape its phase expects."""
    _drive_first_turn(protocol)
    answers = {}
    for objective in ("culture", "character-person"):
        answers[objective] = _serve(
            protocol,
            _body(
                protocol.cfg.dreamer.model,
                f'<dreamer_objective name="{objective}">close</dreamer_objective>',
                tools=("character_no_change", "character_revise"),
            ),
        )
        assert protocol.phase == 3
    # The culture call used its verb; the character session took the
    # designed no-tool close — different occasions, different answers.
    assert _call(answers["culture"])[0] == "character_no_change"
    assert _content(answers["character-person"])
    assert _content(
        _serve(
            protocol,
            _body(protocol.cfg.llm.model, "<standing_prior>prev</standing_prior>", role="system"),
        )
    )  # the fold still lands on its own phase
    assert protocol.phase == 4 and protocol.background == 2
    assert protocol.occasions["culture"] == 1
    assert protocol.occasions["character-person"] == 1
    assert not protocol.errors


def test_wrong_schema_unknown_and_exhausted_requests_are_named_errors(protocol):
    """Each refusal lands in `errors` under its kind, the request stays
    captured in `requests`, and a valid request after it still serves."""

    def refused(body, kind):
        before = protocol.phase
        status, response = protocol.dispatch(body)
        assert status == 422 and protocol.phase == before
        assert protocol.errors[-1][0] == kind
        return response

    # Wrong schema: the offered `note_create` rejects the generated args.
    refused(
        _body(
            protocol.cfg.dreamer.model,
            f'<m n="1">{INITIAL}</m>',
            tools=(
                _tool(
                    "note_create",
                    {
                        "type": "object",
                        "properties": {"title": {"type": "integer"}},
                        "required": ["title", "body"],
                    },
                ),
                "remind",
            ),
        ),
        "schema_mismatch",
    )
    # Wrong shape: an unstreamed call on the streamed dream lane.
    refused(_body(protocol.cfg.dreamer.model, "x", stream=False), "mismatched_shape")
    # Unknown model lane, unknown objective, unsupported occasion.
    refused(_body("generated-other", "x"), "unknown_model")
    refused(
        _body(
            protocol.cfg.dreamer.model,
            '<dreamer_objective name="profile-person">x</dreamer_objective>',
        ),
        "unsupported_objective",
    )
    refused(_body(protocol.cfg.relevance_gate.model, "x", stream=False), "unsupported_occasion")
    assert len(protocol.requests) == 5  # every refused request stayed captured
    # Positive control: the plan itself still drives.
    _drive(protocol)
    # Exhausted: the plan is spent; an extra call names its phase.
    refused(_body(protocol.cfg.dreamer.model, "x"), "exhausted")
    assert len(protocol.requests) == 17


def _judge_body(payload, model="generated-a"):
    return {"model": model, "stream": False,
            "messages": [{"role": "user", "content":
                          "<cited_sources>\n" + json.dumps(payload)
                          + "\n</cited_sources>"}]}


def _behavior_payload(check, *, polarity="must", outcome="delivered",
                      said=(), calls=(), items=(), declared="the declared input"):
    return {"measure": "behavior",
            "expectation": {"turn_index": 2, "polarity": polarity,
                            "kind": "behavior", "text": "the claim",
                            "check": check},
            "declared_input": {"text": declared},
            "turn_outcome": {"status": outcome, "said": list(said)},
            "effects": {"status": "observed", "items": list(items), "tools": []},
            "tool_calls": list(calls),
            "final_output": ""}


def _call_args(name, arguments):
    # The digested payload shape `judges._tool_calls` emits: outcome under
    # `result.status`, refusal beside it — never the trajectory row's own keys.
    return {"tool": name, "arguments": arguments,
            "result": {"status": "ok"}, "refusal": None}


def test_the_behavior_claim_binds_the_turns_own_output():
    """Positive controls, both anchored shapes: a delivered turn's own
    `send_reply` and a `recorded` turn's own session `remind` each carry
    their claim's check token — each answers `met` on ITS evidence."""
    judge = GeneratedJudge()
    reply = _behavior_payload("Cedar Hall", said=[ANSWER], calls=[
        _call_args("send_reply", {"text": ANSWER})])
    assert judge.answer(_judge_body(reply))["verdict"] == "met"
    scheduled = _behavior_payload(
        "Which generated venue and time", outcome="recorded", calls=[
            _call_args("remind", {"text": QUESTION})])
    assert judge.answer(_judge_body(scheduled))["verdict"] == "met"


def test_a_wrong_observed_reply_fails_its_claim():
    """The claim is judged on the turn's own output, not on anything the
    window held: a `must` whose token the bound output lacks is
    `not_met`, and a `must_not` whose token it carries is `not_met`."""
    judge = GeneratedJudge()
    wrong = _behavior_payload("Cedar Hall", said=["The venue is Oak Room."],
                              calls=[_call_args("send_reply",
                                                {"text": "The venue is Oak Room."})])
    assert judge.answer(_judge_body(wrong))["verdict"] == "not_met"
    broke_restraint = _behavior_payload(
        "has taken place", polarity="must_not",
        said=["The generated gathering has taken place."],
        calls=[_call_args("send_reply",
                          {"text": "The generated gathering has taken place."})])
    assert judge.answer(_judge_body(broke_restraint))["verdict"] == "not_met"


@pytest.mark.parametrize("mutate", [
    {"outcome": "unconfirmed"}, {"declared": None}, {"check": None},
])
def test_incomplete_turn_evidence_reads_cannot_tell(mutate):
    """An unbound outcome, a missing declared input or a claim with no
    check token is `cannot_tell` — the finite judge never guesses."""
    judge = GeneratedJudge()
    check = mutate.pop("check", "Cedar Hall")
    payload = _behavior_payload(check, said=[ANSWER], **mutate)
    assert judge.answer(_judge_body(payload))["verdict"] == "cannot_tell"


def test_the_judge_is_reached_by_declared_model_never_by_stream_flag(tmp_path, cfg):
    """An unstreamed non-judge call — the gate, an unknown lane — belongs to
    the protocol's named errors; only the two eval aliases reach the judge."""
    plan = JourneyServices(policy="", judge_models=("generated-a", "generated-b"))
    plan.protocol, plan.judge = make_causal_protocol(cfg), GeneratedJudge()
    assert plan._destination({"model": "generated-a"}) == plan.judge.respond
    assert plan._destination({"model": "generated-b"}) == plan.judge.respond
    for model in (cfg.llm.model, cfg.dreamer.model, cfg.relevance_gate.model, "generated-unknown"):
        for stream in (True, False):
            assert plan._destination({"model": model, "stream": stream}) == plan.protocol.respond


def test_the_declared_world_keeps_production_cadence_and_lanes(tmp_path):
    """The world's own overrides reach its composed config, while its timers,
    thresholds and model lanes are the deployment's — nothing shortened,
    nothing suppressed, no route invented."""
    plan = JourneyServices(policy=_POLICY, overrides=dict(_OVERRIDES))
    cfg = plan._declared_config("sqlite://", tmp_path)
    assert cfg.obs.metrics.port == plan._metrics_port
    assert cfg.cognition.enabled and cfg.heartbeat.enabled
    assert cfg.dreamer.enabled and cfg.culture.enabled and cfg.standing.enabled
    assert cfg.llm.model == "nora" and cfg.dreamer.model == "nora-dreamer"
    assert cfg.relevance_gate.model == "nora-gate"
    assert cfg.heartbeat.tick_seconds == 60
    assert cfg.cognition.tick_seconds == 30
    assert cfg.cognition.cue_quiet_seconds == 120
    assert cfg.dreamer.tick_seconds == 300
    assert cfg.reminders.min_lead_seconds == 120
