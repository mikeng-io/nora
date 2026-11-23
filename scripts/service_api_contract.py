"""Generate the service API's OpenAPI 3.1 document from `nora.domain.service_api*`.

usage: python -m scripts.service_api_contract           # rewrite the document
       python -m scripts.service_api_contract --check   # exit 1 with the diff when stale
"""

from __future__ import annotations

import difflib
import json
import re
import sys
from pathlib import Path
from typing import Any, get_args

from pydantic import TypeAdapter
from pydantic.json_schema import JsonSchemaMode

from nora.domain import service_api as api
from nora.domain import service_api_platform as platform
from nora.domain import service_api_voice as voice

ROOT = Path(__file__).resolve().parents[1]
DOCUMENT = ROOT / "docs" / "service-api" / "openapi.json"
ROUTES = platform.PLATFORM_ROUTES + voice.VOICE_ROUTES

_COMPONENTS: dict[str, Any] = {
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
    **{
        tp.__name__: tp
        for route in ROUTES
        for tp in (route.request, route.response)
        if tp is not None and tp is not bytes
    },
}
_PARAM = re.compile(r"\{([a-z][a-z0-9_]*)\}")
_OCTETS = {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}


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


def _outcome_statuses(schemas: dict[str, Any]) -> None:
    families = {"ReadOutcome": api.ReadOutcome, "OperationOutcome": api.OperationOutcome}
    for name, family in families.items():
        schemas[name]["x-http-status"] = {o: api.status_for(o) for o in get_args(family)}


def _effects(schemas: dict[str, Any]) -> None:
    """Each request names the effect its kind carries; an open effect admits more fields."""
    for request, table in voice.EFFECTS.items():
        schemas[request.__name__]["x-effects"] = {k: _ref(tp) for k, tp in table.items()}
    for effect in voice.OPEN_EFFECTS:
        schemas[effect.__name__]["additionalProperties"] = True


def _ref(tp: type) -> dict[str, Any]:
    return {"$ref": f"#/components/schemas/{tp.__name__}"}


def _responses(route: platform.Route) -> dict[str, Any]:
    """Each status the route's family can answer with an envelope, then the bare ones."""
    family = api.ReadOutcome if route.family == "read" else api.OperationOutcome
    out: dict[str, Any] = {}
    for status in sorted({api.status_for(o) for o in get_args(family)}):
        outcomes = [o for o in get_args(family) if api.status_for(o) == status]
        content: dict[str, Any] = {"application/json": {"schema": _ref(route.response)}}
        if route.answers_bytes and status == 200:
            content.update(_OCTETS)
        out[str(status)] = {"description": ", ".join(outcomes), "content": content}
    for status in sorted(api.NO_ENVELOPE_STATUSES):
        if status != 409 or route.conflicts:
            out[str(status)] = {"description": "no envelope"}
    return out


def _paths() -> dict[str, Any]:
    paths: dict[str, Any] = {}
    for route in ROUTES:
        operation: dict[str, Any] = {
            "operationId": route.name,
            "x-family": route.family,
            "responses": _responses(route),
        }
        if names := _PARAM.findall(route.path):
            operation["parameters"] = [
                {"name": n, "in": "path", "required": True, "schema": {"type": "string"}}
                for n in names
            ]
        if route.request is bytes:
            operation["requestBody"] = {"required": True, "content": _OCTETS}
        elif route.request is not None:
            operation["requestBody"] = {
                "required": True,
                "content": {"application/json": {"schema": _ref(route.request)}},
            }
        paths.setdefault(route.path, {})[route.method.lower()] = operation
    return paths


def generate() -> str:
    """The document's exact text, deterministic for a given set of domain types."""
    keyed: list[tuple[str, JsonSchemaMode, TypeAdapter[Any]]] = [
        (name, "serialization", TypeAdapter(tp)) for name, tp in _COMPONENTS.items()
    ]
    top, defs = TypeAdapter.json_schemas(keyed, ref_template="#/components/schemas/{model}")
    schemas: dict[str, Any] = dict(defs.get("$defs", {}))
    for (name, _), schema in top.items():
        schemas.setdefault(name, schema)
    _outcome_statuses(schemas)
    _effects(schemas)
    document = {
        "openapi": "3.1.0",
        "info": {"title": "Nora service API", "version": "1"},
        "paths": _paths(),
        "components": {"schemas": _wire_names(schemas)},
    }
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    expected = generate()
    if "--check" not in argv:
        DOCUMENT.parent.mkdir(parents=True, exist_ok=True)
        DOCUMENT.write_text(expected, encoding="utf-8")
        return 0
    actual = DOCUMENT.read_text(encoding="utf-8") if DOCUMENT.exists() else ""
    if actual == expected:
        return 0
    rel = DOCUMENT.relative_to(ROOT)
    sys.stdout.writelines(difflib.unified_diff(
        actual.splitlines(keepends=True), expected.splitlines(keepends=True),
        fromfile=f"{rel} (committed)", tofile=f"{rel} (generated)",
    ))
    print(f"{rel} is stale: run python -m scripts.service_api_contract", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
