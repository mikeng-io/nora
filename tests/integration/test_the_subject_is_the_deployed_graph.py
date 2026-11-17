"""`obtain()` returns what the deployment runs — proven by driving it, not by reading it.

The graph `nora.subject.obtain` hands back is the same object `nora/__main__.py` runs,
with the entry points present as attributes, so an eval never reaches the model with
something production does not send.

Obtaining costs about 64ms per call against the template-cloned database, which is what
makes per-case obtaining affordable and a case plain data.
"""
from __future__ import annotations

import dataclasses
import os

import pytest

from nora.app.composition import Dependencies
from nora.subject import SubjectSpec, obtain


@pytest.fixture
def spec(tmp_path, clean_pg_url, monkeypatch) -> SubjectSpec:
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
    fields = {f.name for f in dataclasses.fields(Dependencies)}
    assert len(fields) == 84, "Dependency shape changed; reconcile the production subject."
    assert {"access", "managed_services", "managed_session"} <= fields
    assert subject.deps.access is subject.deps.ingest._access


@pytest.mark.asyncio
async def test_the_entry_points_are_attributes_not_assemblies(spec) -> None:
    """`design/evaluation/subject.md` load-bearing claim, held.

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


@pytest.mark.asyncio
async def test_two_subjects_do_not_share_an_environment_variable(
    clean_pg_url, tmp_path, monkeypatch,
) -> None:
    """`obtain` gives each call its OWN variable rather than borrowing the
    deployment's and putting it back.

    Restoring cannot be made safe: it runs after the whole of
    `build_dependencies`, which awaits extensively, so two interleaved callers
    leave the process pointing at a database that is gone. A unique name removes
    the race rather than serialising it — and leaves the deployment's own
    variable untouched, which this asserts.
    """
    import os

    from nora.subject import SubjectSpec, obtain

    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://untouched/db")

    a = await obtain(SubjectSpec(
        database_url=clean_pg_url, workdir=tmp_path / "a", room=("signal", "g-a"),
    ))
    b = await obtain(SubjectSpec(
        database_url=clean_pg_url, workdir=tmp_path / "b", room=("signal", "g-b"),
    ))
    assert os.environ["DATABASE_URL"] == "postgresql+asyncpg://untouched/db"
    # The returned config NAMES NO VARIABLE. Each call used one of its own and deleted it,
    # so a config still pointing at that name would be a live trap: anything re-reading
    # `os.environ[database_url_env]` gets a KeyError. `Subject.database_url` is the field
    # to read.
    assert a.cfg.store.database_url_env == ""
    assert b.cfg.store.database_url_env == ""
    assert a.database_url == b.database_url == clean_pg_url
    assert not [k for k in os.environ if k.startswith("NORA_SUBJECT_DATABASE_URL_")]


@pytest.mark.asyncio
async def test_a_subjects_signal_send_lands_as_production_s_does(spec) -> None:
    """The sandbox has no signal-cli, and production's send answers `sent`. Without
    `landed_signal_sends` in `obtain`, every send here fails and `send_reply` tells the
    model `refused` on every reply."""
    from nora.domain.models import ConversationTarget
    from nora.subject.sandbox_signal import LandedSignalSender

    subject = await obtain(spec)
    try:
        signal = subject.deps.registry.get("signal")
        assert isinstance(signal._sender, LandedSignalSender)
        result = await signal.send(
            target=ConversationTarget(
                platform="signal", conversation_id="g1", conversation_type="group"),
            text="hi",
        )
        assert result.ok
    finally:
        await subject.deps.background.cancel_all()


@pytest.mark.asyncio
async def test_a_subject_never_holds_the_embedding_worker(spec) -> None:
    """`[memory.index] embed` stays as deployed, because it also switches the
    graph surface's query embedding on; the worker, its write half, is never
    the subject's, so no sandbox writes a vector through the live embedder."""
    from nora.app.composition import build_dependencies
    from nora.subject.obtain import build_config

    on = dataclasses.replace(
        spec, overrides={"memory.index.read": True, "memory.index.embed": True},
    )
    subject = await obtain(on)
    assert subject.cfg.memory.index.embed is True
    assert subject.deps.memory_embed is None
    # Positive control: the same config composed as production does holds one.
    cfg = build_config(dataclasses.replace(on, workdir=on.workdir / "control"))
    deps = await build_dependencies(cfg)
    assert deps.memory_embed is not None
