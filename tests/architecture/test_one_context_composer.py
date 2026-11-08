"""Typed context boundary and the shrinking legacy cleanup backlog.

This inventories constructor calls, not source flow. Mypy checks body types;
the runtime checks the actual caller even through aliases or dynamic dispatch.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from nora.domain.blocks import ContextBlock, wrap_outside_text
from nora.domain.composed import (
    COMPOSERS,
    LEGACY_COMPOSERS,
    Composed,
    compose_body,
    legacy_composed,
)

ROOT = Path(__file__).resolve().parents[2]

# Historical ceiling from the known-violation inventory, not permission to add sites.
_INITIAL_LEGACY = frozenset(
    {
        "nora.agent.prefill:_context_header",
        "nora.agent.prefill:_replying_to_block",
        "nora.agent.prefill:_standing_block",
        "nora.agent.prefill:build_prefill",
        "nora.agent.prefill:build_tag_message",
        "nora.agent.speech_format:render_input",
        "nora.app.profile_spike:event_judge",
        "nora.ingest.relevance_gate:render_context",
        "nora.language.character:CharacterNoraAdaptor.inputs",
        "nora.language.character:CharacterPersonAdaptor.inputs",
        "nora.language.character:CharacterUnitAdaptor.inputs",
        "nora.language.character:_social_block",
        "nora.language.character:room_cultures_block",
        "nora.language.confirmation_render:confirmation_prompts",
        "nora.language.culture:CultureAdaptor.inputs",
        "nora.language.lessons:LessonsAdaptor.inputs",
        "nora.language.profile:ProfilePersonAdaptor.inputs",
        "nora.language.profile_collective:ProfileCollectiveAdaptor.inputs",
        "nora.language.profile_collective:ProfileCollectiveAdaptor.inputs.source_block",
        "nora.language.profile_verdict:ProfileVerdictAdaptor.inputs",
        "nora.subject.context_selector_render:selector_prompt",
    }
)


def legacy_sites(source: str, module: str) -> set[str]:
    found: set[str] = set()

    def walk(node: ast.AST, scope: str = "") -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scope = (scope + "." + node.name).strip(".")
        if (
            isinstance(node, ast.Call)
            and ast.unparse(node.func).split(".")[-1] == "legacy_composed"
        ):
            found.add(module + ":" + scope)
        for child in ast.iter_child_nodes(node):
            walk(child, scope)

    walk(ast.parse(source))
    return found


def validate_legacy(sites: set[str], allowed: frozenset[str]) -> None:
    assert allowed <= _INITIAL_LEGACY
    assert sites == allowed, f"unlisted or stale legacy sites: {sites ^ allowed}"


def test_legacy_allowlist_only_shrinks() -> None:
    assert not LEGACY_COMPOSERS & COMPOSERS
    found = set()
    for path in (ROOT / "nora").rglob("*.py"):
        module = str(path.relative_to(ROOT).with_suffix("")).replace("/", ".")
        found |= legacy_sites(path.read_text(), module)
    validate_legacy(found, LEGACY_COMPOSERS)


def test_unlisted_legacy_site_is_rejected() -> None:
    found = legacy_sites("def new(rows):\n return legacy_composed(str(rows))", "nora.new")
    with pytest.raises(AssertionError, match="unlisted or stale"):
        validate_legacy(set(LEGACY_COMPOSERS) | found, LEGACY_COMPOSERS)
    with pytest.raises(AssertionError):
        validate_legacy(set(LEGACY_COMPOSERS) | found, LEGACY_COMPOSERS | found)
    with pytest.raises(TypeError, match="unlisted legacy"):
        legacy_composed("raw rows")


def test_raw_str_body_raises() -> None:
    with pytest.raises(TypeError, match="requires a Composed"):
        ContextBlock(name="notes", body="raw")  # type: ignore[arg-type]


def test_registered_composer_output_is_accepted() -> None:
    rendered = wrap_outside_text("media_text", "synthetic", "hello <world>")
    assert "hello &lt;world&gt;" in rendered
    from nora.testing.context_blocks import compose_fixture

    value = compose_fixture("hello")
    block = ContextBlock(name="notes", body=value)
    assert block.body.text == "hello"
    assert block.body.composer == "nora.testing.context_blocks:compose_fixture"
    assert "hello" in block.render()


def test_unregistered_composer_and_direct_construction_are_rejected() -> None:
    with pytest.raises(TypeError, match="unregistered context"):
        compose_body("raw")
    with pytest.raises(TypeError, match="only by registered"):
        Composed("raw", "invented", _key=object())


def test_composed_construction_stays_inside_the_boundary() -> None:
    for path in (ROOT / "nora").rglob("*.py"):
        if path == ROOT / "nora/domain/composed.py":
            continue
        tree = ast.parse(path.read_text())
        assert not any(
            isinstance(n, ast.Call) and ast.unparse(n.func).split(".")[-1] == "Composed"
            for n in ast.walk(tree)
        ), path
