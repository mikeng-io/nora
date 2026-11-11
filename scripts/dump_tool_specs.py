"""Dump every built native `ToolSpec`, in order, to canonical JSON + sha256.

Baseline for the tools.py dissolution (ADR 0037): the built tool surface must
be byte-identical before and after the move. Builds the registry's own
"everything, for introspection" path — `ToolRegistry._introspection_specs()`
— the same builder `catalog()` and `tool_param_names()` read, so the dump
covers the DECLARED surface (Discord platform verbs, Dreamer-only memory
verbs, both memory surfaces), not one turn's subset.

Per spec the dump carries:

- `name`, `description`, `schema` — the ToolSpec's declared fields;
- `doc` — the callable's verbatim docstring (Strands turns the text above
  `Args:` into the model-facing description, so this IS the surface);
- `signature` — every parameter's name, annotation repr and default repr in
  order (the `inputSchema` Strands derives);
- every metadata field (`budget`, `terminal`, `time_shiftable`,
  `disclose_failure`, `timeout_seconds`, `side_effect_state`, `idempotency`,
  `retry`, `output_contract`).

Run: `uv run python -m scripts.dump_tool_specs [out.json]`.
Prints the sha256 of the JSON (which is also written to out.json, default
`specs.json`). Compare hashes across commits — they must match byte-for-byte.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import sys

from nora.agent.ports import adapt
from nora.agent.registry import ToolRegistry
from nora.testing import FakeBrowserRunner, FakeMemoryStore, FakeSandboxRuntime


def _specs() -> list[dict]:
    # The full declared surface: every optional collaborator wired, mirroring
    # `test_the_registry_exposes_every_declared_tool`'s registry. `object()`
    # sentinels are enough — introspection builds, never calls.
    registry = ToolRegistry(
        record=object(),
        rollup=object(),
        standing_head=object(),
        culture=object(),
        social_items=object(),
        memos=object(),
        computer=FakeSandboxRuntime(),
        browser=FakeBrowserRunner(),
        documents=object(),
        computer_admission=object(),
        browser_manager=object(),
    )
    out = []
    for spec in registry._introspection_specs():
        call = spec.call
        try:
            sig = inspect.signature(call)
            params = [
                {
                    "name": p.name,
                    "kind": str(p.kind),
                    "annotation": repr(p.annotation),
                    "default": repr(p.default),
                }
                for p in sig.parameters.values()
            ]
        except (TypeError, ValueError):
            params = None
        try:
            strands_spec = adapt(spec).tool_spec
        except Exception:
            strands_spec = None
        out.append(
            {
                "name": spec.name,
                "strands": strands_spec,
                "description": spec.description,
                "schema": spec.schema,
                "doc": getattr(call, "__doc__", None),
                "signature": params,
                "budget": spec.budget,
                "terminal": spec.terminal,
                "time_shiftable": spec.time_shiftable,
                "disclose_failure": spec.disclose_failure,
                "timeout_seconds": spec.timeout_seconds,
                "side_effect_state": spec.side_effect_state,
                "idempotency": spec.idempotency,
                "retry": spec.retry,
                "output_contract": spec.output_contract,
            }
        )
    return out


def main() -> int:
    out_path = sys.argv[1] if len(sys.argv) > 1 else "specs.json"
    data = json.dumps(_specs(), indent=2, sort_keys=True) + "\n"
    with open(out_path, "w") as f:
        f.write(data)
    print(f"{hashlib.sha256(data.encode()).hexdigest()}  {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
