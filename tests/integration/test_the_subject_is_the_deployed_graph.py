"""`obtain()` returns what the deployment runs — proven by driving it, not by reading it.

`design/evaluation/03-step0-results.md` measured seven eval-side assemblies, four
of which reached the model with something production does not send. This is the
control on the replacement: the graph `nora.subject.obtain` hands back is the
same object `nora/__main__.py` runs, with the entry points present as attributes.

Costs ~64ms per call against the template-cloned database (§3 of that file),
which is what makes per-case obtaining affordable and a case plain data.
"""
from __future__ import annotations

import dataclasses
import os

import pytest

from nora.app.composition import Dependencies
from nora.subject import SubjectSpec, obtain


@pytest.fixture
def spec(tmp_path, pg_url, monkeypatch) -> SubjectSpec:
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    # No identity.toml or policy.toml written here, deliberately: `obtain` owns
    # them, because a caller writing them by hand is how every caller ends up
    # with a slightly different Nora.
    return SubjectSpec(database_url=os.environ["DATABASE_URL"], workdir=tmp_path)


@pytest.mark.asyncio
async def test_obtain_returns_the_production_graph(spec) -> None:
    """Same type, same builder, same field count as `nora/__main__.py` gets."""
    subject = await obtain(spec)
    assert isinstance(subject.deps, Dependencies)
    assert len(dataclasses.fields(Dependencies)) == 38, (
        "Dependencies changed shape; the subject follows it automatically, but "
        "this number is here so a change is noticed rather than absorbed."
    )


@pytest.mark.asyncio
async def test_the_entry_points_are_attributes_not_assemblies(spec) -> None:
    """`01-boundaries` §3's load-bearing claim, held.

    The entry point is a SELECTION on a case — `deps.dispatcher`,
    `deps.participation_trigger`, `deps.dreamer_runtime` — not a branch in a
    runner and not something a caller builds. If any of these stopped being an
    attribute, a suite would have to assemble one, which is the defect."""
    deps = (await obtain(spec)).deps
    for name in ("dispatcher", "engagement", "participation_trigger",
                 "dreamer_runtime", "registry", "assembler"):
        assert hasattr(deps, name), f"entry point {name!r} is not on Dependencies"


@pytest.mark.asyncio
async def test_two_subjects_are_independent(spec, tmp_path) -> None:
    """Per-case obtaining is only safe if two do not share state.

    The alternative — one session-scoped subject with a reset seam — is the
    architecture this bundle avoided BECAUSE obtaining is cheap. That argument
    only holds while this is true."""
    a = await obtain(spec)
    b = await obtain(dataclasses.replace(spec, workdir=tmp_path / "second"))
    assert a.deps is not b.deps
    assert a.cfg.store.data_dir != b.cfg.store.data_dir


@pytest.mark.asyncio
async def test_overrides_reach_the_composed_graph(spec) -> None:
    """The one seam that is configuration rather than a collaborator.

    A caller says which model, which doctrine, how many samples — and it must
    actually reach the graph, or a suite silently measures the template's values.
    That is not hypothetical: a worktree with no `config.toml` has been measuring
    `config.example.toml`'s upstream ids for as long as anyone has run a suite
    from one."""
    subject = await obtain(dataclasses.replace(spec, overrides={"llm.model": "nora"}))
    assert subject.cfg.llm.model == "nora"
