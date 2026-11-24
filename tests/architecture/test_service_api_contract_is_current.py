"""The committed OpenAPI document is what the domain types generate, in wire names."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from scripts.service_api_contract import DOCUMENT

ROOT = Path(__file__).resolve().parents[2]
ENVELOPE_PARTS = {"outcome", "payload", "when", "from", "scope", "trace"}
_SNAKE = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*")


def _properties(node: object):
    if isinstance(node, list):
        for item in node:
            yield from _properties(item)
    elif isinstance(node, dict):
        yield from (node.get("properties") or {}).items()
        for value in node.values():
            yield from _properties(value)


def test_the_committed_contract_is_current():
    result = subprocess.run(
        [sys.executable, "-m", "scripts.service_api_contract", "--check"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_envelope_keys_are_the_wire_keys():
    schemas = json.loads(DOCUMENT.read_text(encoding="utf-8"))["components"]["schemas"]
    envelopes = [name for name in schemas if name.endswith("Envelope")]
    assert {"ReadEnvelope", "OperationEnvelope", "PlatformOperationEnvelope",
            "VoiceOperationEnvelope", "IntakeOperationEnvelope"} <= set(envelopes)
    for envelope in envelopes:
        assert set(schemas[envelope]["properties"]) == ENVELOPE_PARTS, envelope
    properties = list(_properties(schemas))
    assert any(name.endswith("_ms") for name, _ in properties)
    for name, schema in properties:
        assert _SNAKE.fullmatch(name), name
        types = {schema.get("type"), *(s.get("type") for s in schema.get("anyOf", []))}
        if "at" in name.split("_"):
            assert name.endswith("_ms"), f"{name} is a time and is not named *_ms"
        if name.endswith("_ms"):
            assert "integer" in types, name
