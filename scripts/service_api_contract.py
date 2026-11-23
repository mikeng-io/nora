"""Generate the service API's OpenAPI 3.1 document from `nora.domain.service_api`.

usage: python -m scripts.service_api_contract           # rewrite the document
       python -m scripts.service_api_contract --check   # exit 1 with the diff when stale
"""

from __future__ import annotations

import difflib
import json
import sys
from pathlib import Path
from typing import Any, get_args

from pydantic import TypeAdapter
from pydantic.json_schema import JsonSchemaMode

from nora.domain import service_api as api

ROOT = Path(__file__).resolve().parents[1]
DOCUMENT = ROOT / "docs" / "service-api" / "openapi.json"

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


def _outcome_statuses(schemas: dict[str, Any]) -> None:
    families = {"ReadOutcome": api.ReadOutcome, "OperationOutcome": api.OperationOutcome}
    for name, family in families.items():
        schemas[name]["x-http-status"] = {o: api.status_for(o) for o in get_args(family)}


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
    document = {
        "openapi": "3.1.0",
        "info": {"title": "Nora service API", "version": "1"},
        "paths": {},
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
