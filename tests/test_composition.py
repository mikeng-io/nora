"""The composition root is the ONE place that reads config. Everything else
receives dependencies. This test pins that property."""

import ast
from pathlib import Path

import pytest

from nora.app.composition import Dependencies, StartupError, build_dependencies
from nora.config import load
from tests.builders import override_config

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    import shutil
    dst = tmp_path / "config.toml"
    shutil.copy(ROOT / "config.example.toml", dst)
    # config.example.toml's identity_file ("identity.toml") resolves relative to
    # the config file's own directory (nora/config.py's path-resolution context);
    # build_dependencies() calls load_persons() against it, so it must exist.
    (tmp_path / "identity.toml").write_text(
        '[persons.miles]\nname = "Miles"\n[[persons.miles.signal]]\nphone = "+852111"\n'
    )
    # policy.toml resolves the same way. `persons = "*"` keeps these wiring
    # tests about the dependency graph rather than the allowlist.
    (tmp_path / "policy.toml").write_text(
        'default = "deny"\n[[conversations]]\nplatform = "signal"\n'
        'id = "g1"\npersons = "*"\n'
    )
    # config.example.toml's store.db_path ("~/workspace/nora/data/messages.db") is
    # absolute, so it survives path resolution untouched regardless of where this
    # copy lives — without this override, build_dependencies() below would wire a
    # SqlAuditStore at the real production database.
    #
    # platforms.signal.account overrides the empty "derive from the daemon" default
    # (config.example.toml) with a fixed value: derive_account() returns an override
    # immediately, with no network call, so build_dependencies() never needs a real
    # (or faked) signal-cli daemon reachable at the example config's placeholder URL.
    #
    # doctrine.dir ("nora/doctrine", config.example.toml) resolves relative to the
    # copied config file's directory (tmp_path here), which has no such subdirectory
    # — point it at the real, already-valid doctrine files instead of copying them.
    return override_config(load(dst), {
        "store.db_path": tmp_path / "messages.db",
        "platforms.signal.account": "+85290000100",
        "doctrine.dir": ROOT / "nora" / "doctrine",
    })


async def test_build_dependencies_returns_the_wired_graph(cfg, tmp_path):
    deps = await build_dependencies(cfg)
    assert isinstance(deps, Dependencies)
    assert deps.audit is not None
    assert deps.memory is not None
    assert deps.persons is not None
    assert deps.metrics is not None
    assert deps.policy is not None
    assert deps.assembler is not None
    assert deps.ingest is not None
    assert deps.engagement is not None
    assert deps.dispatcher is not None


async def test_the_doctrine_the_root_builds_carries_the_generated_capability(cfg):
    """DEC-29: the composition root is where the registry meets the doctrine.

    Everything else about the capability section is testable in isolation and
    means nothing if this wiring is missing — the store would assemble four
    sections, the prompt would say nothing about what she can do, and every
    unit test would still pass. So: the REAL graph, the REAL registry, and the
    assembled block asserted to name every registered tool.
    """
    from nora.agent.registry import ToolRegistry

    deps = await build_dependencies(cfg)
    text = deps.doctrine.doctrine_text
    assert '<section kind="capability" name="capability"' in text
    for name in ToolRegistry().tool_names():
        assert f"- {name} — " in text, f"{name} is registered but not in the doctrine"


async def test_the_runtime_context_carries_the_configured_language_tag(cfg):
    """The composition root must hand the loader `doctrine.language_hint`, or
    every tag's runtime_context ships a blank `language:` line — the model is
    then never told, from outside, what to speak. This was a live defect: the
    DoctrineStore had the parameter and defaulted it to "", and the root never
    passed it. The value is a BCP 47 tag (RFC 5646), asserted here against the
    real assembled runtime_context. Drop `language_hint=` from the root and the
    line reads `language:` with nothing after it, and this goes red.
    """
    deps = await build_dependencies(cfg)
    assert "language: yue-Hant-HK" in deps.doctrine.runtime_context
    assert "language: \n" not in deps.doctrine.runtime_context


async def test_the_root_hands_the_loader_the_tool_names_its_gate_needs(cfg, tmp_path):
    """The catalogue gate's backticked half is inert without them, and an inert
    gate that nobody notices is worse than no gate.

    It has to be a tool whose name is an ordinary English word — `now` or
    `about`. Every other native is snake_case, which the STRUCTURAL detector
    catches on its own, so injecting one of those would make this pass whether
    or not `tool_names` was ever handed over. This used to inject `recall`;
    DEC-39 renamed that tool to `about`, which is still in the same class.
    """
    import shutil


    d = tmp_path / "doctrine"
    shutil.copytree(ROOT / "nora" / "doctrine", d)
    conduct = d / "conduct.md"
    conduct.write_text(conduct.read_text().replace(
        "# Honesty", "Reach for `about` when you need to.\n\n# Honesty", 1))
    with pytest.raises(StartupError, match="may not name a tool"):
        await build_dependencies(override_config(cfg, {"doctrine.dir": d}))


async def test_no_enabled_platform_refuses_to_start(cfg):
    """`platforms.signal.enabled = false` is honoured now (nora.platform.registry
    .AdapterRegistry) — previously (config.py's "NOT YET HONOURED" comment)
    SignalAdapter started regardless. With no platform enabled there is nothing
    for the engagement pipeline to send through, so this is a startup failure,
    not a degraded start."""
    disabled = override_config(cfg, {"platforms.signal.enabled": False})
    with pytest.raises(StartupError, match="no platform adapter is enabled"):
        await build_dependencies(disabled)


def test_build_dependencies_never_wires_the_real_database(cfg, tmp_path):
    """Regression guard: config.example.toml's store.db_path is absolute, so a
    fixture that forgets to override it would build a real SqlAuditStore against
    the production database instead of a throwaway one. tmp_path being a parent
    of the resolved db_path is what actually proves that didn't happen here."""
    assert tmp_path in cfg.store.db_path.parents


async def test_dependencies_do_not_expose_settings(cfg):
    """A stage must never be able to reach the whole config through its deps —
    that is how a god object grows back."""
    deps = await build_dependencies(cfg)
    assert not hasattr(deps, "config")
    assert not hasattr(deps, "settings")


def _resolved_module_name(rel: Path) -> str:
    """The dotted module name `nora/<rel>` would have at import time, e.g.
    `infra/sql/migrations_runner.py` -> `nora.infra.sql.migrations_runner`."""
    parts = rel.with_suffix("").parts
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(("nora", *parts))


def _resolve_import_from(module_name: str, node: ast.ImportFrom) -> str:
    """Resolve an `ast.ImportFrom` node's target to a fully-qualified dotted name,
    following Python's own relative-import rule so `from ..config import X` inside
    a deeply nested module resolves to the package it actually names — not to
    whatever text happens to follow the dots."""
    if node.level == 0:
        return node.module or ""
    # node.level dots: 1 means "this module's own package"; each extra dot climbs
    # one more level. A non-package module's own package is its dotted name minus
    # its last component.
    package_parts = module_name.split(".")[:-1]
    base_parts = package_parts[: len(package_parts) - (node.level - 1)]
    if node.module:
        base_parts = [*base_parts, node.module]
    return ".".join(base_parts)


def test_only_app_and_main_import_config():
    """Enforced properly by import-linter too (contract 3); asserted here as an
    AST-level check so the rule also has a home in the suite, with no allowlist."""
    offenders = []
    for p in (ROOT / "nora").rglob("*.py"):
        rel = p.relative_to(ROOT / "nora")
        if rel.parts[0] == "app" or str(rel) in ("__main__.py", "config.py"):
            continue
        module_name = _resolved_module_name(rel)
        tree = ast.parse(p.read_text())
        for n in ast.walk(tree):
            if not isinstance(n, ast.ImportFrom):
                continue
            resolved = _resolve_import_from(module_name, n)
            if resolved == "nora.config" or resolved.startswith("nora.config."):
                offenders.append(str(rel))
    assert not offenders, f"config imported outside app/ and __main__: {offenders}"
