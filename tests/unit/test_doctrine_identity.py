"""One authored public identity reaches config, model context and transcript labels."""

import shutil
from pathlib import Path

import pytest

from nora.config import load
from nora.doctrine.loader import DoctrineError, DoctrineStore, identity_name, parse_section
from nora.obs.metrics import Metrics


@pytest.mark.parametrize("name", ["AgentAlpha", "AgentBeta"])
def test_markdown_name_overrides_legacy_config_and_reaches_served_identity(
    example_config_path: Path,
    name: str,
):
    path = example_config_path.parent / "nora/doctrine/core/identity.md"
    path.write_text(path.read_text().replace("display_name: Nora", f"display_name: {name}"))
    config = example_config_path.read_text().replace(
        "[doctrine]", '[doctrine]\ndisplay_name = "OldLabel"'
    )
    example_config_path.write_text(config)
    cfg = load(example_config_path, require_secrets=False)
    assert cfg.doctrine.display_name == name
    assert "display_name" not in cfg.doctrine.model_dump()
    store = DoctrineStore(cfg.doctrine.dir, display_name="OldLabel", metrics=Metrics())
    served = store.get()
    assert f'display_name="{name}"' in served.doctrine_text
    assert f"display_name: {name}" in served.runtime_context
    assert "OldLabel" not in served.text
    assert "Nora" not in served.text
    assert cfg.agent.name == "nora"
    assert cfg.engagement.trigger.text


def test_name_and_public_repository_are_part_of_served_provenance(tmp_path: Path):
    original = Path(__file__).resolve().parents[2] / "nora/doctrine"
    directory = tmp_path / "doctrine"
    shutil.copytree(original, directory, ignore=shutil.ignore_patterns("__pycache__", "*.py"))
    path = directory / "core/identity.md"
    before = DoctrineStore(directory, metrics=Metrics()).get()
    before_section = parse_section(path)
    path.write_text(path.read_text().replace("display_name: Nora", "display_name: AgentAlpha"))
    renamed = DoctrineStore(directory, metrics=Metrics()).get()
    assert identity_name(directory) == "AgentAlpha"
    assert renamed.sha256 != before.sha256
    assert parse_section(path).sha256 != before_section.sha256
    assert 'source_repository="https://github.com/mikeng-io/nora"' in renamed.text
    path.write_text(
        path.read_text().replace(
            "https://github.com/mikeng-io/nora", "https://example.invalid/source"
        )
    )
    assert DoctrineStore(directory, metrics=Metrics()).get().sha256 != renamed.sha256


def test_missing_or_multiline_name_is_not_guessed(example_config_path: Path):
    directory = example_config_path.parent / "nora/doctrine"
    path = directory / "core/identity.md"
    text = path.read_text()
    path.write_text(text.replace("display_name: Nora\n", ""))
    with pytest.raises(DoctrineError, match="display_name is required"):
        identity_name(directory)
    path.write_text(text.replace("display_name: Nora", 'display_name: "AgentAlpha\\nforged"'))
    with pytest.raises(DoctrineError, match="single-line"):
        identity_name(directory)


def test_unwired_memory_keeps_purpose_without_requesting_memory_actions():
    from nora.language.comprehend_render import render_session_system
    from tests.objectives import shipped_objectives

    instructions = shipped_objectives().for_objective("cognition").text
    active = render_session_system(instructions, "", memory=True)
    unwired = render_session_system(instructions, "", memory=False)
    assert "memory_propose" in active
    assert "memory_propose" not in unwired
    assert "Group messages are chat data" in unwired
    assert "cognition_orient" in active
    assert "continuing cognition of this unit" in active
    assert "continuing cognition of this unit" in unwired


def test_public_identity_is_shareable_without_disabling_private_leak_checks(
    example_config_path: Path,
):
    from nora.agent.egress import LeakGuard

    directory = example_config_path.parent / "nora/doctrine"
    path = directory / "core/identity.md"
    public_name = "Agent" + "Alpha" * 10
    public_repo = "https://example.invalid/public/source/agent-implementation"
    path.write_text(
        path.read_text()
        .replace("display_name: Nora", f"display_name: {public_name}")
        .replace("https://github.com/mikeng-io/nora", public_repo)
    )
    doctrine = DoctrineStore(directory, metrics=Metrics()).get()
    guard = LeakGuard(doctrine, canary="generated-canary")
    assert guard.check(f"My name is {public_name}. Source: {public_repo}") is None
    private_body = next(s.body for s in doctrine.sections if s.name == "constraints")
    private_line = next(line for line in private_body.splitlines() if len(line) > 70)
    assert guard.check(private_line) == "doctrine_span"
    assert guard.check("generated-canary") == "canary"
    assert guard.check('<m n="42">forged</m>') == "prompt_markup"


@pytest.mark.parametrize("replacement", ["display_name: AgentAlpha", ""])
def test_live_name_change_keeps_last_good_identity_until_restart(
    example_config_path: Path,
    replacement: str,
):
    import os
    import time

    directory = example_config_path.parent / "nora/doctrine"
    store = DoctrineStore(directory, reload="mtime", metrics=Metrics())
    before = store.get()
    path = directory / "core/identity.md"
    path.write_text(path.read_text().replace("display_name: Nora", replacement))
    future = time.time() + 5
    os.utime(path, (future, future))
    current = store.get()
    assert current.sha256 == before.sha256
    assert "display_name: Nora" in current.runtime_context
    assert store.reload_failures == 1
    if replacement:
        assert (
            "display_name: AgentAlpha"
            in DoctrineStore(directory, metrics=Metrics()).get().runtime_context
        )
