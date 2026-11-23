"""The committed contract document lists the `voice` and `intake` wire the domain types declare."""

from __future__ import annotations

import json
from typing import get_args

from nora.domain.service_api import (
    NO_ENVELOPE_STATUSES,
    OperationOutcome,
    ReadOutcome,
    path_conforms,
    status_for,
)
from nora.domain.service_api_voice import (
    EFFECTS,
    IntakeOperationKind,
    IntakeOperationRequest,
    VoiceOperationKind,
    VoiceOperationRequest,
    VoiceQueryKind,
)
from nora.infra.service_api_schema import VOICE_ANSWERS, validator
from scripts.service_api_contract import DOCUMENT

THE_WIRE = {
    ("post", "/v1/voice/operations"),
    ("get", "/v1/voice/operations/{operation_id}"),
    ("post", "/v1/voice/queries"),
    ("post", "/v1/intake/operations"),
    ("get", "/v1/intake/operations/{operation_id}"),
}
VOICE_OPERATION_KINDS = {
    "join", "leave", "play", "stop", "pause", "resume", "set_volume", "speak", "report",
}
VOICE_QUERY_KINDS = {"status"}
INTAKE_OPERATION_KINDS = {"record"}


def _document() -> dict:
    return json.loads(DOCUMENT.read_text(encoding="utf-8"))


def _kinds(schema: dict) -> set[str]:
    """A closed kind list: an `enum`, or the `const` of a one-member kind."""
    return set(schema["enum"]) if "enum" in schema else {schema["const"]}


def test_every_documented_path_conforms():
    paths = _document()["paths"]
    documented = {(m, p) for p, methods in paths.items() for m in methods
                  if p.startswith(("/v1/voice/", "/v1/intake/"))}
    assert documented == THE_WIRE
    for _, path in documented:
        assert path_conforms(path), path


def test_each_route_answers_its_family_statuses():
    paths = _document()["paths"]
    for method, path in THE_WIRE:
        operation = paths[path][method]
        family = ReadOutcome if operation["x-family"] == "read" else OperationOutcome
        enveloped = {str(status_for(o)) for o in get_args(family)}
        bare = {str(s) for s in NO_ENVELOPE_STATUSES
                if s != 409 or (method == "post" and path.endswith("/operations"))}
        assert set(operation["responses"]) == enveloped | bare, (method, path)
        for status in bare:
            assert "content" not in operation["responses"][status], (method, path, status)


def test_the_voice_and_intake_kinds_are_closed():
    schemas = _document()["components"]["schemas"]
    declared = {
        "voice operation": set(get_args(VoiceOperationKind)),
        "voice query": set(get_args(VoiceQueryKind)),
        "intake operation": set(get_args(IntakeOperationKind)),
    }
    documented = {
        "VoiceOperationKind": ("voice operation", _kinds(schemas["VoiceOperationKind"])),
        "VoiceOperationRequest": ("voice operation", _kinds(
            schemas["VoiceOperationRequest"]["properties"]["kind"])),
        "VoiceOperationRequest x-effects": ("voice operation", set(
            schemas["VoiceOperationRequest"]["x-effects"])),
        "VoiceQueryKind": ("voice query", _kinds(schemas["VoiceQueryKind"])),
        "VoiceQuery": ("voice query", _kinds(schemas["VoiceQuery"]["properties"]["kind"])),
        "IntakeOperationKind": ("intake operation", _kinds(schemas["IntakeOperationKind"])),
        "IntakeOperationRequest": ("intake operation", _kinds(
            schemas["IntakeOperationRequest"]["properties"]["kind"])),
        "IntakeOperationRequest x-effects": ("intake operation", set(
            schemas["IntakeOperationRequest"]["x-effects"])),
    }
    for schema, (family, kinds) in documented.items():
        assert kinds == declared[family], (
            f"{schema} does not document the declared kinds: regenerate")
    assert set(EFFECTS[VoiceOperationRequest]) == declared["voice operation"]
    assert set(EFFECTS[IntakeOperationRequest]) == declared["intake operation"]
    assert declared == {
        "voice operation": VOICE_OPERATION_KINDS,
        "voice query": VOICE_QUERY_KINDS,
        "intake operation": INTAKE_OPERATION_KINDS,
    }


def _settled(payload: dict) -> dict:
    return {"outcome": "complete", "payload": payload,
            "when": {"state": "settled", "started_at_ms": 1, "settled_at_ms": 2},
            "from": {"target": "g1", "platform_ref": None},
            "scope": {"level": "installation"}, "trace": ""}


def test_a_kind_admits_only_its_own_effect():
    """A `report` carrying a join's fields is the app's 400, so the schema refuses
    it; the report's open effect still admits the event's own fields."""
    request = validator("VoiceOperationRequest")
    join = {"guild_id": "g1", "channel_id": "c1"}
    report = {"event_id": "e1", "event_seq": 1, "type": "track-finished",
              "guild_id": "g1", "playback_id": "p1"}

    assert not request.is_valid({"operation_id": "o1", "kind": "report", "effect": join})
    assert not request.is_valid({"operation_id": "o1", "kind": "join",
                                 "effect": {**join, "generation": "x"}})
    assert request.is_valid({"operation_id": "o1", "kind": "report", "effect": report})
    assert request.is_valid({"operation_id": "o1", "kind": "join", "effect": join})


def test_a_report_answer_and_a_connection_answer_never_pass_for_each_other():
    answer = validator("VoiceOperationEnvelope")

    assert not answer.is_valid(_settled({"status": "garbled"}))
    assert not answer.is_valid(_settled({"status": "ok", "state": None}))
    assert answer.is_valid(_settled({"status": "duplicate"}))
    assert answer.is_valid(_settled({"state": {"generation": "gen-1", "channel_id": "c1"}}))
    assert answer.is_valid(_settled({"error": {"kind": "denied", "message": ""}}))


def test_each_kind_complete_carries_what_the_kind_returns():
    """A connection kind's `complete` is the snapshot (its kernel fields required);
    a report's is the hub's status. Refusals keep `error` without a snapshot."""
    connection, report = validator(VOICE_ANSWERS["join"]), validator(VOICE_ANSWERS["report"])
    snapshot = {"generation": "gen-1", "channel_id": "c1", "voice": "connected"}

    for payload in ({}, {"state": {}}, {"state": None}, {"status": "ok"}):
        assert not connection.is_valid(_settled(payload)), payload
    assert connection.is_valid(_settled({"state": snapshot}))
    assert connection.is_valid({**_settled({"state": None, "error": {
        "kind": "unknown_guild", "message": ""}}), "outcome": "rejected"})
    for payload in (None, {}, {"status": "garbled"}, {"state": snapshot}):
        assert not report.is_valid(_settled(payload)), payload
    assert report.is_valid(_settled({"status": "stale"}))


def test_an_ok_status_carries_the_snapshot():
    status = validator("VoiceStatusReadEnvelope")
    read = {"when": {"observed_at_ms": 1, "known_by": "controller_clock"},
            "from": {"source": "voice-controller", "healthy": True, "failed_sources": []},
            "scope": {"level": "installation"}, "trace": ""}

    assert not status.is_valid({**read, "outcome": "ok", "payload": {}})
    assert not status.is_valid({**read, "outcome": "ok", "payload": None})
    assert status.is_valid({**read, "outcome": "nothing_found", "payload": None})
    assert status.is_valid({**read, "outcome": "ok", "payload": {
        "generation": None, "channel_id": None, "connected": False}})
