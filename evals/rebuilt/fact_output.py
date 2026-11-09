"""Wire schemas for fixed-reference memory judges; semantics stay in the policy."""
from __future__ import annotations

from typing import Literal


def response_format(role: Literal["support", "coverage"]) -> dict:
    proof = {
        "type": "object",
        "properties": {
            "statements": {"type": "array", "items": {"type": "integer"}},
            "reads": {"type": "array", "items": {"type": "integer"}},
        },
        "required": ["statements", "reads"],
        "additionalProperties": False,
    }
    if role == "support":
        properties = {
            "verdict": {"type": "string", "enum": ["supported", "unsupported", "unsure"]},
            "reason": {"type": "string"},
            "eligibility": {"type": "string", "enum": ["eligible", "ineligible", "unsure"]},
            "eligibility_reason": {"type": "string"},
        }
    else:
        properties = {
            "coverage": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["covered", "missed", "unsure"]},
                    "reason": {"type": "string"},
                    "covered_by": {"anyOf": [proof, {"type": "null"}]},
                },
                "required": ["id", "verdict", "reason", "covered_by"],
                "additionalProperties": False,
            }},
            "diagnostic_misses": {"type": "array", "items": {"type": "string"}},
        }
    return {"type": "json_schema", "json_schema": {
        "name": "memory_" + role,
        "strict": True,
        "schema": {"type": "object", "properties": properties,
                   "required": list(properties), "additionalProperties": False},
    }}
