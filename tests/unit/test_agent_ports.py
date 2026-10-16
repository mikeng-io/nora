"""`ToolSpec`/`adapt` — plain, framework-free tool
description and the one boundary that turns it into a Strands tool object.
"""

from __future__ import annotations

import dataclasses

import pytest

from nora.agent.ports import ToolSpec, adapt


async def _noop(**kwargs) -> str:
    return "ok"


def _spec(**overrides) -> ToolSpec:
    fields = {"name": "noop", "description": "", "schema": {}, "call": _noop}
    fields.update(overrides)
    return ToolSpec(**fields)


def test_tool_spec_is_frozen():
    spec = _spec()
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.name = "other"  # type: ignore[misc]


def test_budget_defaults_to_one():
    assert _spec().budget == 1


def test_disclose_failure_defaults_to_false():
    assert _spec().disclose_failure is False


def test_timeout_seconds_defaults_to_none():
    assert _spec().timeout_seconds is None


def test_a_tool_can_declare_a_larger_budget():
    assert _spec(budget=3).budget == 3


async def test_adapt_produces_a_callable_strands_tool():
    async def read_thing(name: str) -> str:
        """Read a thing.

        Args:
            name: The thing's name.
        """
        return f"read {name}"

    spec = _spec(name="read_thing", call=read_thing)
    tool = adapt(spec)

    assert tool.tool_name == "read_thing"
    assert await tool._tool_func(name="widget") == "read widget"


async def test_adapt_derives_description_and_schema_from_the_docstring_when_empty():
    async def read_thing(name: str) -> str:
        """Read a thing by name, at some length that clears the schema floor.

        Args:
            name: The thing's name.
        """
        return name

    tool = adapt(_spec(name="read_thing", call=read_thing))

    assert "Read a thing by name" in tool.tool_spec["description"]
    assert "name" in tool.tool_spec["inputSchema"]["json"]["properties"]


async def test_adapt_honors_an_explicit_description_and_schema_override():
    schema = {"type": "object", "properties": {}, "required": []}
    tool = adapt(_spec(name="noop", description="Custom description.", schema=schema))

    assert tool.tool_spec["description"] == "Custom description."
    assert tool.tool_spec["inputSchema"]["json"] == schema
