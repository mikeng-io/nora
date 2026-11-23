"""Copy the intake envelope schemas out of the service API document.

The wake gate validates Nora's intake answers against the contract, but its
image holds only this directory, so it ships a generated subset of
`docs/service-api/openapi.json`: the two intake envelopes and every schema
they reference, unchanged.

usage: python scripts/intake_schema.py           # rewrite wakegate/intake_schema.json
       python scripts/intake_schema.py --check   # exit 1 when it has drifted
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
DOCUMENT = HERE.parents[1] / "docs" / "service-api" / "openapi.json"
TARGET = HERE / "wakegate" / "intake_schema.json"
ROOTS = ("IntakeOperationEnvelope", "IntakeOperationReadEnvelope")
_PREFIX = "#/components/schemas/"


def _refs(node: object) -> set[str]:
    if isinstance(node, dict):
        found = {node["$ref"][len(_PREFIX):]} if isinstance(node.get("$ref"), str) else set()
        return found.union(*(_refs(value) for value in node.values()))
    if isinstance(node, list):
        return set().union(*(_refs(value) for value in node))
    return set()


def extract(document: dict) -> str:
    schemas = document["components"]["schemas"]
    wanted, todo = set(), list(ROOTS)
    while todo:
        name = todo.pop()
        if name not in wanted:
            wanted.add(name)
            todo.extend(_refs(schemas[name]))
    subset = {"components": {"schemas": {name: schemas[name] for name in sorted(wanted)}}}
    return json.dumps(subset, indent=2, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    expected = extract(json.loads(DOCUMENT.read_text()))
    if "--check" in argv:
        if not TARGET.is_file() or TARGET.read_text() != expected:
            print(f"{TARGET.name} is stale: run python scripts/intake_schema.py", file=sys.stderr)
            return 1
        return 0
    TARGET.write_text(expected)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
