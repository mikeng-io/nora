"""Render the service API's OpenAPI 3.1 document from `nora.infra.service_api_schema`.

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

from nora.domain import service_api as api
from nora.domain import service_api_platform as platform
from nora.infra.service_api_schema import ANSWERS, ROUTES, component_schemas

ROOT = Path(__file__).resolve().parents[1]
DOCUMENT = ROOT / "docs" / "service-api" / "openapi.json"

_PARAM = re.compile(r"\{([a-z][a-z0-9_]*)\}")
_OCTETS = {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}


def _ref(tp: type) -> dict[str, Any]:
    return {"$ref": f"#/components/schemas/{tp.__name__}"}


def _responses(route: platform.Route) -> dict[str, Any]:
    """Each status the route's family can answer with an envelope, then the bare ones."""
    family = api.ReadOutcome if route.family == "read" else api.OperationOutcome
    out: dict[str, Any] = {}
    for status in sorted({api.status_for(o) for o in get_args(family)}):
        outcomes = [o for o in get_args(family) if api.status_for(o) == status]
        content: dict[str, Any] = {"application/json": {"schema": {
            "$ref": f"#/components/schemas/{ANSWERS[route.name]}"}}}
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
    document = {
        "openapi": "3.1.0",
        "info": {"title": "Nora service API", "version": "1"},
        "paths": _paths(),
        "components": {"schemas": component_schemas()},
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
