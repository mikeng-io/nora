"""The service API's component schemas, generated from the kernel's types.

One generator for every contract (`platform`, `voice`, `intake`):
`docs/service-api/openapi.json` renders it (`scripts/service_api_contract.py`)
and every client validates its answers by it, so the document and the check
cannot drift. Built once, at import.
"""

from __future__ import annotations

from typing import Any, Final, cast, get_args

from jsonschema import Draft202012Validator
from pydantic import TypeAdapter
from pydantic.json_schema import JsonSchemaMode

from nora.domain import service_api as api
from nora.domain import service_api_platform as platform
from nora.domain import service_api_voice as voice

ROUTES: Final = platform.PLATFORM_ROUTES + voice.VOICE_ROUTES
REF: Final = "#/components/schemas/{model}"
_COMPONENTS: Final[dict[str, Any]] = {
    "Contract": api.Contract,
    "ReadOutcome": api.ReadOutcome,
    "OperationOutcome": api.OperationOutcome,
    "OperationState": api.OperationState,
    "ScopeLevel": api.ScopeLevel,
    "ReadEnvelope": api.ReadEnvelope,
    "OperationEnvelope": api.OperationEnvelope,
    "OperationRequest": api.OperationRequest,
    "OperationRecord": api.OperationRecord,
    "PlatformOperationKind": platform.PlatformOperationKind,
    "PlatformQueryKind": platform.PlatformQueryKind,
    "VoiceOperationKind": voice.VoiceOperationKind,
    "VoiceQueryKind": voice.VoiceQueryKind,
    "IntakeOperationKind": voice.IntakeOperationKind,
    "VoicePlayState": voice.VoicePlayState,
    "VoiceJoinState": voice.VoiceJoinState,
    **{
        tp.__name__: tp
        for route in ROUTES
        for tp in (route.request, route.response)
        if tp is not None and tp is not bytes
    },
}


def _wire_names(node: object) -> object:
    """Drop the trailing underscore a Python keyword field carries (`from_` is `from`)."""
    if isinstance(node, list):
        return [_wire_names(item) for item in node]
    if not isinstance(node, dict):
        return node
    out = {key: _wire_names(value) for key, value in node.items()}
    if isinstance(properties := out.get("properties"), dict):
        out["properties"] = {k.removesuffix("_"): v for k, v in properties.items()}
    if isinstance(required := out.get("required"), list):
        out["required"] = [k.removesuffix("_") for k in required]
    return out


def component_schemas() -> dict[str, Any]:
    """Every component, keyed by name, as the wire names it."""
    keyed: list[tuple[str, JsonSchemaMode, TypeAdapter[Any]]] = [
        (name, "serialization", TypeAdapter(tp)) for name, tp in _COMPONENTS.items()
    ]
    top, defs = TypeAdapter.json_schemas(keyed, ref_template=REF)
    schemas: dict[str, Any] = dict(defs.get("$defs", {}))
    for (name, _), schema in top.items():
        schemas.setdefault(name, schema)
    families = {"ReadOutcome": api.ReadOutcome, "OperationOutcome": api.OperationOutcome}
    for name, family in families.items():
        schemas[name]["x-http-status"] = {o: api.status_for(o) for o in get_args(family)}
    wired = cast(dict[str, Any], _wire_names(schemas))
    for route in platform.PLATFORM_ROUTES:
        for name in _refs(wired[route.response.__name__]["properties"]["payload"]):
            _answer_carries_every_field(wired, name)
    for name in ("ReadWhen", "ReadFrom"):
        wired[name]["required"] = sorted(wired[name]["properties"])
    for name in [n for n in wired if n.endswith("Envelope")] + list(_CLOSED_PARTS):
        wired[name]["additionalProperties"] = False
    _bind_effects(wired)
    _bind_answers(wired)
    wired[BLOB_PUT] = _blob_put(wired["OperationEnvelope"])
    return wired


def _when(outcome: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"if": {"properties": {"outcome": {"const": outcome}}},
            "then": {"properties": {"payload": payload}}}


def _bind_answers(schemas: dict[str, Any]) -> None:
    """Give each voice kind an answer whose `complete` carries what that kind returns."""
    base = schemas["VoiceOperationEnvelope"]
    for tp, fields in voice.MATCHED_IDS.items():
        for field in fields:
            schemas[tp.__name__]["properties"][field]["minLength"] = 1
    for component, tp in VOICE_ANSWER_TYPES.items():
        payload = (_ref(tp) if tp is voice.VoiceReportPayload else
                   {"type": "object", "required": ["state"], "properties": {"state": _ref(tp)}})
        schemas[component] = {
            **base, "title": component, "allOf": [_when("complete", payload)],
            "description": f"A voice operation's answer whose `complete` is {tp.__name__}."}
    schemas["VoiceOperationRequest"]["x-answers"] = {
        kind: {"$ref": REF.format(model=VOICE_ANSWERS[kind])} for kind in voice.VOICE_COMPLETE}
    schemas["VoiceStatusReadEnvelope"]["allOf"] = [
        _when("ok", _ref(voice.VoiceConnectionState))]


def _bind_effects(schemas: dict[str, Any]) -> None:
    """Bind each request's `kind` to its one effect; only `OPEN_EFFECTS` stay open."""
    for request, table in voice.EFFECTS.items():
        schema = schemas[request.__name__]
        schema["x-effects"] = {kind: _ref(tp) for kind, tp in table.items()}
        schema["allOf"] = [
            {"if": {"required": ["kind"], "properties": {"kind": {"const": kind}}},
             "then": {"properties": {"effect": _ref(tp)}}}
            for kind, tp in table.items()]
        for effect in set(table.values()):
            schemas[effect.__name__]["additionalProperties"] = effect in voice.OPEN_EFFECTS


def _ref(tp: type) -> dict[str, str]:
    return {"$ref": REF.format(model=tp.__name__)}


#: An envelope's parts and the delivery and voice payloads carry exactly their
#: fields, so a report's answer and a connection's never pass for each other; a
#: payload a platform may extend (the account, the status) stays open.
_CLOSED_PARTS: Final = ("ReadWhen", "ReadFrom", "OperationWhen", "OperationFrom", "Scope",
                        "PlatformOperationPayload", "VoiceOperationPayload",
                        "VoiceReportPayload")
#: The blob `PUT` answer: an operation envelope whose `complete` names a key and
#: whose `rejected` says why.
BLOB_PUT: Final = "BlobPutEnvelope"
#: Per voice kind, the answer component a caller validates by: the route's own
#: schema with `complete` held to what the kind returns.
VOICE_ANSWER_TYPES: Final[dict[str, type]] = {
    "VoiceConnectionEnvelope": voice.VoiceConnectionState,
    "VoicePlayEnvelope": voice.VoicePlayState,
    "VoiceJoinEnvelope": voice.VoiceJoinState,
    "VoiceReportEnvelope": voice.VoiceReportPayload}
VOICE_ANSWERS: Final[dict[str, str]] = {
    kind: next(name for name, tp in VOICE_ANSWER_TYPES.items() if tp is done)
    for kind, done in voice.VOICE_COMPLETE.items()}
#: The component each route answers with, by route name.
ANSWERS: Final[dict[str, str]] = {
    route.name: BLOB_PUT if route.name == "put_blob" else route.response.__name__
    for route in ROUTES}


def _blob_put(envelope: dict[str, Any]) -> dict[str, Any]:
    return {**envelope, "title": BLOB_PUT, "description": "The answer to a blob PUT.", "allOf": [
        _when("complete", {"type": "object", "required": ["key"],
                          "properties": {"key": {"type": "string"}}}),
        _when("rejected", {"type": "object", "required": ["error"],
                          "properties": {"error": {"$ref": REF.format(model="OperationError")}}}),
    ]}


def _refs(node: object) -> list[str]:
    """The component names a schema node refers to, at any depth."""
    if isinstance(node, list):
        return [name for item in node for name in _refs(item)]
    if not isinstance(node, dict):
        return []
    own = [node["$ref"].rsplit("/", 1)[1]] if isinstance(node.get("$ref"), str) else []
    return own + [name for key, value in node.items() if key != "$ref" for name in _refs(value)]


def _answer_carries_every_field(schemas: dict[str, Any], name: str) -> None:
    """Require every property of an answer's payload type and the types it nests."""
    # A bridge always writes each field (null where the type allows it); an
    # envelope nested as a payload keeps its own rule.
    if name.endswith("Envelope"):
        return
    properties = schemas[name].get("properties", {})
    schemas[name]["required"] = sorted(properties)
    for name_ in _refs(list(properties.values())):
        _answer_carries_every_field(schemas, name_)


_SCHEMAS: Final = component_schemas()


def validator(component: str) -> Draft202012Validator:
    """A compiled validator for one component, its references resolved in place."""
    return Draft202012Validator(
        {"$ref": REF.format(model=component), "components": {"schemas": _SCHEMAS}})
