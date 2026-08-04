"""The composition root is the ONE place that reads config. Everything else
receives dependencies. This test pins that property."""

from pathlib import Path

import pytest

from nora.app.composition import Dependencies, build_dependencies
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
        '[persons.miles]\nname = "Miles"\n[persons.miles.platforms]\nsignal = "+852111"\n'
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
    # persona.dir ("nora/persona", config.example.toml) resolves relative to the
    # copied config file's directory (tmp_path here), which has no such subdirectory
    # — point it at the real, already-valid persona files instead of copying them.
    return override_config(load(dst), {
        "store.db_path": tmp_path / "messages.db",
        "platforms.signal.account": "+85290000100",
        "persona.dir": ROOT / "nora" / "persona",
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


@pytest.mark.xfail(
    reason=(
        "true finding, not a bug in the test: nora/agent/factory.py still does "
        "`from ..config import LimitsConfig, LLMConfig, LLMTierConfig`. Separately "
        "(not caught by this AST-import check, but the same disease): "
        "nora/handler.py's Handler.__init__ takes the *whole* `cfg: Config` object "
        "and reaches deep into it (engagement, persona, store, platforms, llm), "
        "and nora/obs/logging.py/setup_logging + nora/obs/tracing.py/setup_tracing "
        "take duck-typed `cfg` sub-configs. Closing all of these — moving factory.py "
        "onto typed collaborators and giving Handler a Dependencies instead of a "
        "Config — is the pipelines phase's work item; see task-4-report.md."
    ),
    strict=True,
)
def test_only_app_and_main_import_config():
    """Enforced properly by import-linter in the next task; asserted here too so the
    rule has a home in the suite."""
    import ast
    offenders = []
    for p in (ROOT / "nora").rglob("*.py"):
        rel = p.relative_to(ROOT / "nora")
        if rel.parts[0] in ("app", "config.py") or str(rel) in ("__main__.py", "config.py"):
            continue
        tree = ast.parse(p.read_text())
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and n.module and "config" in (n.module or ""):
                offenders.append(str(rel))
    assert not offenders, f"config imported outside app/ and __main__: {offenders}"
