"""The committed contract document lists the `platform` wire the domain types declare."""

from __future__ import annotations

import json
from typing import get_args

import pytest

from nora.domain.service_api import path_conforms
from nora.domain.service_api_platform import PlatformOperationKind, PlatformQueryKind
from nora.infra.service_api_schema import (
    MUSIC_ANSWERS,
    MUSIC_READ_ANSWERS,
    component_schemas,
    validator,
)
from scripts.service_api_contract import DOCUMENT

THE_WIRE = {
    ("post", "/v1/platform/operations"),
    ("get", "/v1/platform/operations/{operation_id}"),
    ("post", "/v1/platform/queries"),
    ("get", "/v1/platform/accounts/{account_id}"),
    ("put", "/v1/platform/blobs/{key}"),
    ("get", "/v1/platform/blobs/{key}"),
    ("get", "/v1/operations/status"),
}
DELIVERY_KINDS = {"send", "react", "edit", "typing"}
MUSIC_KINDS = {"music_play", "music_skip", "music_top", "music_stop", "music_set_volume"}
OPERATION_KINDS = DELIVERY_KINDS | MUSIC_KINDS
QUERY_KINDS = {
    "roster", "members", "channels", "channel",
    "member_presence", "voice_presence", "groups", "music_status",
}
ROOM = {"conversation_id": "room-1"}
PLAY = {**ROOM, "sender_ids": ["sender-1"], "query_or_url": "a track"}
TOP = {**ROOM, "position": 2}
OWNER = {"kind": "live_invocation", "id": "owner-1"}
TRACK = {"title": "a track", "url": "https://example.com/track"}
MUSIC_RESULT = {**TRACK, "queued": False, "percent": None, "error": None}
DELIVERY_RESULT = {"platform_refs": [], "platform_ms": None, "message_id": None, "error": None}
REJECTED_KINDS = ("nothing_found", "nothing_playing", "nothing_queued_at_position")
UNAVAILABLE_KINDS = (
    "disconnected", "not_in_voice", "join_failed", "not_in_allowed_guild",
    "volume_out_of_range", "music_failed", "ledger_full",
)


def _document() -> dict:
    return json.loads(DOCUMENT.read_text(encoding="utf-8"))


def test_every_documented_path_conforms():
    paths = _document()["paths"]
    documented = {(m, p) for p, methods in paths.items() for m in methods
                  if p.startswith(("/v1/platform/", "/v1/operations/"))}
    assert documented == THE_WIRE
    for _, path in documented:
        assert path_conforms(path), path


def test_the_operation_and_query_kinds_are_closed():
    schemas = _document()["components"]["schemas"]
    declared = {
        "operation": set(get_args(PlatformOperationKind)),
        "query": set(get_args(PlatformQueryKind)),
    }
    documented = {
        "PlatformOperationKind": ("operation", schemas["PlatformOperationKind"]["enum"]),
        "PlatformOperationRequest": (
            "operation", schemas["PlatformOperationRequest"]["properties"]["kind"]["enum"]),
        "PlatformQueryKind": ("query", schemas["PlatformQueryKind"]["enum"]),
        "PlatformQuery": ("query", schemas["PlatformQuery"]["properties"]["kind"]["enum"]),
    }
    for schema, (family, kinds) in documented.items():
        assert set(kinds) == declared[family], (
            f"{schema} does not document the declared kinds: regenerate")
    assert declared == {"operation": OPERATION_KINDS, "query": QUERY_KINDS}


def test_the_document_maps_music_kinds_to_their_answers():
    schemas = component_schemas()
    request = schemas["PlatformOperationRequest"]
    assert request.get("x-answers") == {
        kind: {"$ref": "#/components/schemas/MusicOperationEnvelope"} for kind in MUSIC_KINDS}
    assert request.get("x-read-answers") == {
        kind: {"$ref": "#/components/schemas/MusicOperationReadEnvelope"} for kind in MUSIC_KINDS}
    assert schemas["PlatformQuery"].get("x-answers") == {
        "music_status": {"$ref": "#/components/schemas/MusicStatusReadEnvelope"}}


@pytest.mark.parametrize(("kind", "effect", "wrong"), [
    ("music_play", PLAY, TOP),
    ("music_skip", ROOM, TOP),
    ("music_top", TOP, PLAY),
    ("music_stop", ROOM, PLAY),
    ("music_set_volume", {**ROOM, "percent": 75}, TOP),
])
def test_a_music_kind_admits_only_its_own_effect(kind, effect, wrong):
    validate = validator("PlatformOperationRequest")
    request = {"operation_id": "operation-1", "kind": kind}

    assert validate.is_valid({**request, "effect": effect})
    assert not validate.is_valid({**request, "effect": wrong})
    assert not validate.is_valid({**request, "effect": {**effect, "extra": True}})


@pytest.mark.parametrize("kind", sorted(DELIVERY_KINDS))
def test_a_delivery_operation_requires_a_non_null_owner(kind):
    validate = validator("PlatformOperationRequest")
    request = {"operation_id": "operation-1", "kind": kind, "effect": ROOM}

    assert validate.is_valid({**request, "owner": OWNER})
    assert not validate.is_valid(request)
    assert not validate.is_valid({**request, "owner": None})


def test_a_music_operation_forbids_an_owner():
    validate = validator("PlatformOperationRequest")
    request = {"operation_id": "operation-1", "kind": "music_play", "effect": PLAY}

    assert validate.is_valid(request)
    assert not validate.is_valid({**request, "owner": OWNER})
    assert not validate.is_valid({**request, "owner": None})


def test_music_status_requires_only_a_room():
    validate = validator("PlatformQuery")
    query = {"kind": "music_status"}

    assert validate.is_valid({**query, "params": ROOM})
    assert not validate.is_valid({**query, "params": {}})
    assert not validate.is_valid({**query, "params": PLAY})


def _operation(payload: dict, outcome: str = "complete") -> dict:
    return {
        "outcome": outcome, "payload": payload,
        "when": {"state": "started"} if outcome == "pending" else {"state": "settled"},
        "from": {"target": "room-1"}, "scope": {"level": "room", "id": "room-1"}, "trace": "",
    }


def _read(payload: object, outcome: str = "ok") -> dict:
    return {
        "outcome": outcome, "payload": payload,
        "when": {"observed_at_ms": 1, "known_by": "bridge_clock"},
        "from": {"source": "bridge", "healthy": True, "failed_sources": []},
        "scope": {"level": "room", "id": "room-1"}, "trace": "",
    }


def _error(kind: str) -> dict:
    return {"kind": kind, "message": ""}


def test_a_complete_music_answer_and_a_delivery_answer_never_pass_for_each_other():
    delivery = validator("PlatformOperationEnvelope")
    music = validator(MUSIC_ANSWERS["music_play"])
    delivered, played = _operation(DELIVERY_RESULT), _operation(MUSIC_RESULT)

    assert delivery.is_valid(delivered)
    assert music.is_valid(played)
    assert not delivery.is_valid(played)
    assert not music.is_valid(delivered)


def test_a_complete_music_answer_requires_the_full_payload_with_null_error():
    validate = validator(MUSIC_ANSWERS["music_play"])

    assert validate.is_valid(_operation(MUSIC_RESULT))
    assert not validate.is_valid(_operation({**MUSIC_RESULT, "error": _error("nothing_found")}))
    for field in MUSIC_RESULT:
        assert not validate.is_valid(_operation({k: v for k, v in MUSIC_RESULT.items()
                                                if k != field})), field
    assert not validate.is_valid(_operation({**MUSIC_RESULT, "note": "extra text"}))


def test_a_rejected_music_answer_requires_a_declared_error_in_its_full_payload():
    validate = validator(MUSIC_ANSWERS["music_play"])

    for kind in REJECTED_KINDS:
        assert validate.is_valid(_operation({**MUSIC_RESULT, "error": _error(kind)}, "rejected"))
    assert not validate.is_valid(_operation({**MUSIC_RESULT, "error": _error("other")}, "rejected"))
    assert not validate.is_valid(_operation({"error": _error("nothing_found")}, "rejected"))
    assert not validate.is_valid(_operation(MUSIC_RESULT, "rejected"))


def test_an_unavailable_music_answer_requires_a_declared_error():
    validate = validator(MUSIC_ANSWERS["music_play"])

    for kind in UNAVAILABLE_KINDS:
        assert validate.is_valid(_operation({"error": _error(kind)}, "unavailable")), kind
    assert not validate.is_valid(_operation({"error": _error("other")}, "unavailable"))


@pytest.mark.parametrize(("outcome", "kind"), [
    ("denied", "room_not_admitted"),
    ("unsupported", "music_disabled"),
    ("unavailable", "music_failed"),
])
def test_a_music_refusal_carries_only_its_error(outcome, kind):
    validate = validator(MUSIC_ANSWERS["music_play"])
    error = _error(kind)

    assert validate.is_valid(_operation({"error": error}, outcome))
    assert not validate.is_valid(_operation({**MUSIC_RESULT, "error": error}, outcome))
    assert not validate.is_valid(_operation({}, outcome))
    assert not validate.is_valid(_operation({"error": None}, outcome))


def test_a_pending_music_answer_carries_an_empty_payload():
    validate = validator(MUSIC_ANSWERS["music_play"])

    assert validate.is_valid(_operation({}, "pending"))
    assert not validate.is_valid(_operation({"error": _error("music_failed")}, "pending"))
    assert not validate.is_valid(_operation({}, "uncertain"))
    assert not validate.is_valid(_operation({}, "incomplete"))


def test_a_music_read_back_carries_its_own_operation_record_on_ok():
    music = validator(MUSIC_READ_ANSWERS["music_play"])
    delivery = validator("OperationReadEnvelope")
    played, delivered = _read(_operation(MUSIC_RESULT)), _read(_operation(DELIVERY_RESULT))

    assert music.is_valid(played)
    assert delivery.is_valid(delivered)
    assert not music.is_valid(delivered)
    assert not delivery.is_valid(played)
    assert not music.is_valid(_read(None))
    for outcome in ("nothing_found", "unavailable"):
        assert music.is_valid(_read(None, outcome))
        assert not music.is_valid({**played, "outcome": outcome})


def test_music_status_carries_the_track_only_on_ok():
    validate = validator(MUSIC_ANSWERS["music_status"])

    assert validate.is_valid(_read(TRACK))
    assert not validate.is_valid(_read(None))
    assert not validate.is_valid(_read({"title": "a track"}))
    assert not validate.is_valid(_read(MUSIC_RESULT))
    for outcome in ("nothing_found", "unavailable"):
        assert validate.is_valid(_read(None, outcome))
        assert not validate.is_valid(_read(TRACK, outcome))


def test_an_account_answer_requires_capabilities():
    validate = validator("AccountReadEnvelope")
    account = {"platform": "discord", "primary": "account-1", "aliases": []}

    assert validate.is_valid(_read({**account, "capabilities": ["music"]}))
    assert validate.is_valid(_read({**account, "capabilities": []}))
    assert not validate.is_valid(_read(account))
