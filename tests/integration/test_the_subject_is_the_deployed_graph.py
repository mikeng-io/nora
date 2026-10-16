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
    assert len(dataclasses.fields(Dependencies)) == 68, (
        "Dependencies changed shape; the subject follows it automatically, but "
        "this number is here so a change is noticed rather than absorbed. "
        "38 -> 40 on 2026-09-17: `relevance_gate` and `fire_path`, the two "
        "deployed ENTRY POINTS that were reachable only through "
        "`participation_trigger`'s privates. Every other entry point was "
        "already a field here; these two were not, which is a fair part of why "
        "measuring them meant building second ones. "
        "40 -> 41 on rebasing onto #332: `resolve_voice_room`, master's own "
        "work — which is the case this number is really for. The subject "
        "follows the graph automatically and would have absorbed it silently. "
        "42 -> 43: `proactive_screen`, the fire-time screen a "
        "`kind=\"proactive\"` row passes before it speaks — reachable only as an "
        "argument inside the heartbeat's constructor call. "
        "41 -> 42: `clock`. The composition root wrote "
        "`lambda: datetime.now(UTC)` at NINE sites while nine components "
        "already took an injectable one, so the graph had the dependency and "
        "no name for it. "
        "43 -> 44 on 2026-09-24: `self_presence`, the one `<presence>` reader "
        "the tag path, the fire path and the proactive pass share — held "
        "three times, reachable only through privates, and what the sandbox "
        "has to stand transport health in on (`nora.subject.transport`). "
        "44 -> 45 on 2026-09-28: `workspaces_of`, a person's Nora workspaces "
        "for the portal's own-memory page (DEC-214), a callable because "
        "`nora.web` never sees a `Policy`. "
        "45 -> 46 on 2026-09-28: `revisions`, the store the daemon records "
        "each doctrine, Dreamer, config and deploy load through "
        "(cognition-loop step 1b), so `nora snapshot --at T` can say what "
        "was in force. "
        "46 -> 49 on 2026-09-28: `wake_evaluator` and `wake_tick_seconds`, the "
        "cognition loop's shadow evaluator (None under `[cognition] mode = "
        "off`), and `health_recorder`, adapter health as Record "
        "(design/cognition-loop step 1). "
        "49 -> 51 on 2026-09-30: `cognition_runner` and `cognition_shutdown`, "
        "the shadow Cognition session and its idempotent stop (None under "
        "`[cognition] session_mode = off`; step 3a-iii), which the eval "
        "subject reads to drive `tool_selection_cognition`. "
        "51 -> 53 on 2026-09-30: `computer_sweeper` and `computer_sweep_seconds`, "
        "the computer session sweeper and its interval (None unless `[computer] "
        "enabled`; design/sandbox-runtime §15.2). "
        "53 -> 56 on 2026-10-01: `documents`, `document_service` and `rooms_of`, "
        "the document store, its publish service and the person -> rooms callable "
        "the portal's read API needs (ADR 0020). "
        "56 -> 57 on 2026-10-01: `blobs`, the content-addressed byte store the "
        "portal's picture route reads a document's pictures from (None when media "
        "custody is off; the route then answers `unavailable`). "
        "57 -> 58 on 2026-10-01: `computer_probe`, the timed probe of computerd "
        "behind `nora_computer_up` (None unless `[computer] enabled`; DEC-261). "
        "58 -> 59 on 2026-10-01: `room_label`, (platform, conversation id) -> the "
        "operator's room label, a callable for the portal's document rows "
        "because `nora.web` never sees a `Policy` (design/artifact §33). "
        "60 -> 59 on 2026-10-03: the delete reconcile sweep removed. "
        "59 -> 60 on 2026-10-04: `subagent_backend`, the sub-agent worker the shutdown "
        "cancels (None unless `[subagent] enabled`; DEC-282). "
        "60 -> 62 on 2026-10-05: `otp` and `otp_api_token`, the one-time code "
        "service and the bearer its routes need (None/empty unless `[otp] enabled`; "
        "DEC-287). "
        "62 -> 63 on 2026-10-08: `artifacts`, `computer_export`'s record, which the "
        "portal's Artifacts routes list and serve for the viewer's rooms (ADR 0023). "
        "63 -> 64 on 2026-10-09: `oauth_keepalive`, the image OAuth lanes' keeper "
        "(DEC-308), which `nora/__main__.py` starts and the subject never does. "
        "64 -> 65 on 2026-10-16: `memory_embed`, the memory index's embedding worker "
        "the drain loop runs (None unless `[memory_index] embed`; "
        "design/unified-memory/65-build-step-3-vectors.md V1). "
        "65 -> 66: `social_poller`, the social stream's poller, which "
        "`nora/__main__.py` starts and the subject never does (None unless "
        "`[social.poll] enabled`). "
        "66 -> 67: `social_perception`, its perception pass, built beside the "
        "poller and started only by `nora/__main__.py` with it, never by the "
        "subject (None whenever the poller is). "
        "67 -> 68: `room_event_shown`, whether a room member may see an event of "
        "the room on the portal's Plans (surprises and lineage), a callable "
        "because `nora.web` never sees a lineage policy."
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


@pytest.mark.asyncio
async def test_two_subjects_do_not_share_an_environment_variable(
    pg_url, tmp_path, monkeypatch,
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
        database_url=pg_url, workdir=tmp_path / "a", room=("signal", "g-a"),
    ))
    b = await obtain(SubjectSpec(
        database_url=pg_url, workdir=tmp_path / "b", room=("signal", "g-b"),
    ))
    assert os.environ["DATABASE_URL"] == "postgresql+asyncpg://untouched/db"
    # The returned config NAMES NO VARIABLE. Each call used one of its own and deleted it,
    # so a config still pointing at that name would be a live trap: anything re-reading
    # `os.environ[database_url_env]` gets a KeyError. `Subject.database_url` is the field
    # to read.
    assert a.cfg.store.database_url_env == ""
    assert b.cfg.store.database_url_env == ""
    assert a.database_url == b.database_url == pg_url
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
    """`[memory_index] embed` stays as deployed, because it also switches the
    graph surface's query embedding on; the worker, its write half, is never
    the subject's, so no sandbox writes a vector through the live embedder."""
    from nora.app.composition import build_dependencies
    from nora.subject.obtain import build_config

    on = dataclasses.replace(
        spec, overrides={"memory_index.enabled": True, "memory_index.embed": True},
    )
    subject = await obtain(on)
    assert subject.cfg.memory_index.embed is True
    assert subject.deps.memory_embed is None
    # Positive control: the same config composed as production does holds one.
    cfg = build_config(dataclasses.replace(on, workdir=on.workdir / "control"))
    deps = await build_dependencies(cfg)
    assert deps.memory_embed is not None
