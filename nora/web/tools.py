"""GET /app/v1/tools — Nora's live tool catalog, read-only.

A thin projection over `ToolRegistry.catalog()` (nora/agent/registry.py),
which is where the strands introspection lives — the strands import
boundary (tests/test_boundaries.py::test_strands_import_boundary) allows
`strands` only under `nora/agent/` and `nora/compute/`, so the web layer
never imports it. The payload IS the deployed agent's own tool surface:
same `build_tool_specs` builder, same `NATIVE_TOOLS` declared fields, so a
tool added on master appears here on the next deploy with no frontend
rebuild (the portal fetches this endpoint fresh, `cache: no-store`).
"""

from __future__ import annotations

from nora.agent.registry import ToolRegistry


def build_tools_payload() -> list[dict]:
    """Every tool the deployed agent can call, model-facing description +
    declared registry fields, sorted by name. `ToolRegistry.catalog()`
    handles the R-27 posture internally (one un-introspectable tool logs and
    drops its description, never sinks the endpoint)."""
    return list(ToolRegistry().catalog())
