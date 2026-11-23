"""The service API's component schemas, generated from the kernel's types.

`docs/service-api/openapi.json` renders them (`scripts/service_api_contract.py`);
the client validates every bridge answer by them, so the document and the check
cannot drift. Built once, at import.
"""

from __future__ import annotations

from typing import Any, Final, cast, get_args

from jsonschema import Draft202012Validator
from pydantic import TypeAdapter
from pydantic.json_schema import JsonSchemaMode

from nora.domain import service_api as api
from nora.domain import service_api_platform as platform

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
    **{
        tp.__name__: tp
        for route in platform.PLATFORM_ROUTES
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
    return wired


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
