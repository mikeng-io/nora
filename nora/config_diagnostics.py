"""Safe validation coordinates and explanations for private TOML loaders.

Custom ValueError explanations in these schemas must contain rules and remedies,
never rejected values. Pydantic's input, context and dynamic map keys stay private.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, get_args, get_origin

from pydantic import BaseModel, ValidationError


def validation_summary(error: ValidationError, model: type[BaseModel], data: Any) -> str:
    messages = []
    for item in error.errors(include_input=False):
        schema: Any = model
        node = data
        coordinate = ""
        for part in item["loc"]:
            args = get_args(schema)
            if args and get_origin(schema) not in (dict, list):
                schema = next((a for a in args if a is not type(None)), schema)
            if isinstance(part, int):
                coordinate += f"[{part}]"
                schema = get_args(schema)[0] if get_args(schema) else Any
                node = node[part] if isinstance(node, list) and part < len(node) else None
            elif isinstance(schema, type) and issubclass(schema, BaseModel):
                field = schema.model_fields.get(part)
                coordinate += ("." if coordinate else "") + (part if field else "entry")
                schema = field.annotation if field else Any
                node = node.get(part) if isinstance(node, Mapping) else None
            elif get_origin(schema) is dict:
                index = list(node).index(part) if isinstance(node, Mapping) and part in node else 0
                coordinate += f"[{index}]"
                schema = get_args(schema)[1]
                node = node.get(part) if isinstance(node, Mapping) else None
            else:
                coordinate += ".entry"
        remedy = item["msg"]
        if item["type"] == "extra_forbidden":
            remedy = "unknown field; remove it or use a field from the example file"
        messages.append(f"{coordinate or 'root'}: {item['type']}: {remedy}")
    return "; ".join(messages)
