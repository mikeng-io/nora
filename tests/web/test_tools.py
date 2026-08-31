"""`/app/v1/tools` payload — the live tool catalog, read-only.

The contract this endpoint holds: what the browser sees IS what
`build_tool_specs` builds for the agent loop (minus plugin tools the
registry never builds itself) — so the portal can never advertise a tool
the model cannot call. Tests pin the shape, the plugin exclusion, and the
declared-field mirror of `NATIVE_TOOLS`, not the prose (which lives in the
docstrings and would churn every tool edit).
"""

from __future__ import annotations

import pytest

from nora.agent.tools import NATIVE_TOOLS
from nora.web.tools import build_tools_payload


def test_catalog_lists_every_built_tool_with_a_description():
    payload = build_tools_payload()
    assert payload, "catalog must not be empty"
    # Every key the builder returns — exactly the non-plugin set.
    built_names = {
        name for name, spec in NATIVE_TOOLS.items() if not spec.plugin
    }
    assert {entry["name"] for entry in payload} == built_names
    for entry in payload:
        assert entry["description"], (
            f"{entry['name']} must carry a model-facing description"
        )


def test_plugin_tools_are_excluded():
    """`skills` is registered in NATIVE_TOOLS only so R-7's default-deny
    admits it — the plugin builds/describes itself. The catalog must not
    claim a tool the registry does not build."""
    payload = build_tools_payload()
    assert "skills" not in {entry["name"] for entry in payload}


def test_declared_fields_mirror_native_tools():
    """The declared registry metadata (`budget`, terminality, contract
    triple, ...) is copied from NATIVE_TOOLS — a portal reader can trust
    these equal what the runtime enforces, because they ARE that table."""
    payload = {entry["name"]: entry for entry in build_tools_payload()}
    fields = (
        "budget", "terminal", "time_shiftable", "source_tier", "cacheable",
        "read_only", "side_effect_state", "idempotency", "retry",
        "output_contract",
    )
    for name, declared in NATIVE_TOOLS.items():
        if declared.plugin:
            continue
        entry = payload[name]
        for field in fields:
            assert getattr(declared, field) == entry[field], (
                f"{name}.{field} drifted from NATIVE_TOOLS"
            )


def test_catalog_is_sorted_by_name():
    names = [entry["name"] for entry in build_tools_payload()]
    assert names == sorted(names)


@pytest.mark.parametrize(
    "name",
    ["now", "media_fetch", "web_fetch", "send_reply", "remember"],
)
def test_representative_tools_present(name):
    """Spot-check a spread of tool kinds (clock, media, web, delivery,
    memory-write) — the catalog is not missing a whole family."""
    assert name in {entry["name"] for entry in build_tools_payload()}
