"""Every eval DRAW reads and writes a Honcho workspace of its own.

A draw that fed one shared `<deployment>-eval` workspace would have the tag path
read it back as the tagger card, so draw 3 of a case would read what draws 1
and 2 had said and answer «third time you've asked». These tests
drive the real `evals.backbone._drive` and the real workspace lifecycle against
`tests.fakes.FakeHoncho`, a server shaped after Honcho's own OpenAPI (a 409 on
deleting a workspace with an active session, a 404 under a workspace that
does not exist), and the last one composes a real subject to show that none
of its memory traffic leaves the draw's workspace.
"""

from __future__ import annotations

import asyncio
import gc
import importlib
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from strands_evals import Case

from evals import backbone
from evals.backbone import NORA_MEMORY_WORKSPACE, sampled
from evals.rebuilt.domain import Outcome
from evals.rebuilt.entry import ENTRY_POINTS, EntryPoint
from nora.infra.honcho import HonchoClient
from nora.subject import SubjectSpec, obtain
from nora.subject.memory_workspace import (
    DRAW_MARKER,
    HonchoWorkspaces,
    NotADrawWorkspaceError,
    draw_workspace,
    sweep_stale_draw_workspaces,
)
from nora.subject.sandbox import Sandboxed
from tests.fakes import FakeHoncho

#: The MODULE: `nora.subject` re-exports the `obtain` function under the
#: same name, so attribute access on the package returns the function.
obtain_module = importlib.import_module("nora.subject.obtain")

_DEPLOYMENT = "nora"
_SHOT = {"platform": "signal", "conversation_id": "g-real", "before_ms": 1_752_000_000_000}


@pytest.fixture
async def honcho(monkeypatch):
    """A fake Honcho, and the subject side pointed at it: `memory_endpoint()`
    is where every draw workspace's server and prefix come from."""
    fake = FakeHoncho()
    url = await fake.start()
    monkeypatch.setattr(obtain_module, "memory_endpoint", lambda: (url, _DEPLOYMENT))
    yield fake
    await fake.close()


class _Deps:
    class _Closable:
        async def cancel_all(self) -> None: ...
        async def aclose(self) -> None: ...
        async def close(self) -> None: ...

    background = audit = client = _Closable()
    doctrine = SimpleNamespace(doctrine_text="doctrine")


def _faked_seams(monkeypatch, *, drive) -> list[Any]:
    """`obtain`, the sandbox database and the snapshot faked; `_drive` and
    `draw_workspace` are real. Returns the SubjectSpecs `obtain` was given."""
    specs: list[Any] = []

    async def fake_obtain(spec: Any) -> Any:
        specs.append(spec)
        return SimpleNamespace(
            deps=_Deps(), room=("signal", "g-real"),
            cfg=SimpleNamespace(engagement=SimpleNamespace(trigger=SimpleNamespace(text="nora"))),
        )

    class _Sandbox:
        def __init__(self, url: str, **_: Any) -> None:
            self._url = url

        async def __aenter__(self) -> str:
            return f"{self._url}_sb"

        async def __aexit__(self, *_: Any) -> None:
            return None

    async def fake_sandbox(_subject: Any, **kwargs: Any) -> Sandboxed:
        return Sandboxed(now_ms=int(kwargs["before_ms"]), rows={}, transcript=())

    monkeypatch.setattr(backbone, "obtain", fake_obtain)
    monkeypatch.setattr(backbone, "sandbox_database", _Sandbox)
    monkeypatch.setattr(backbone, "sandbox_from_snapshot", fake_sandbox)
    monkeypatch.setitem(ENTRY_POINTS, "dreamer", EntryPoint(drive=drive))
    return specs


def _ws(spec: SubjectSpec) -> str:
    """The Honcho workspace a SubjectSpec composes against."""
    return (spec.overrides or {})["honcho.workspace_id"]


def _spec() -> backbone.Spec:
    return backbone.Spec(
        source_url="postgresql://u:p@h/src_eval", entry="dreamer",
        identity="[persons.miles_novak]\n", policy='default = "deny"\n',
    )


async def test_each_draw_gets_its_own_workspace_created_empty_and_deleted(
    honcho, monkeypatch,
) -> None:
    """Three draws of one case: three workspaces, each existing and EMPTY
    while its draw runs (peer cards empty),
    each deleted afterwards, and each named on the draw's own reading."""
    seen_during: list[tuple[str, bool, list, list]] = []

    async def drive(_subject: Any, _case: Any, *, now_ms: int) -> Outcome:
        workspace = _ws(specs[-1])
        seen_during.append((
            workspace, workspace in honcho.workspaces,
            list(honcho.writes.get(workspace, [])), list(honcho.sessions.get(workspace, {})),
        ))
        return Outcome(admitted=None)

    specs = _faked_seams(monkeypatch, drive=drive)
    base = Case(name="asks-twice", input="x", metadata={"snapshot": dict(_SHOT)})
    results = [await backbone._drive(draw, _spec()) for draw in sampled(base, 3)]

    workspaces = [_ws(spec) for spec in specs]
    assert len(set(workspaces)) == 3, workspaces
    assert all(w.startswith(f"{_DEPLOYMENT}-eval-") for w in workspaces)
    assert all(f"{backbone.RUN_ID}-asks-twice-{i}-" in w for i, w in enumerate(workspaces, 1))
    assert [(w, True, [], []) for w in workspaces] == seen_during, (
        "each draw's workspace must exist, and hold nothing, while the draw runs"
    )
    assert honcho.deleted == workspaces, "every draw's workspace is deleted when it ends"
    assert not any(w in honcho.workspaces for w in workspaces)
    created = [r for r in honcho.requests if r[0] == "POST" and r[1] == "/v3/workspaces"]
    assert [r[2] for r in created] == workspaces
    for result, workspace in zip(results, workspaces, strict=True):
        state = {e.name: e.state for e in result["environment_state"]}
        assert state[NORA_MEMORY_WORKSPACE] == workspace


async def test_a_draw_workspace_is_created_with_derivation_off(honcho) -> None:
    """A draw can never read what the deriver derives from it (derivation
    lags tens of minutes), so the create asks Honcho for no reasoning and no
    summaries: no queue load on the Honcho production's memory runs on."""
    admin = HonchoWorkspaces(honcho.url, deployment=_DEPLOYMENT)
    async with draw_workspace("derive-probe#1", run_id="run-d", admin=admin) as draw:
        configuration = honcho.workspaces[draw.id]["configuration"]
    assert configuration == {"reasoning": {"enabled": False}, "summary": {"enabled": False}}


async def test_a_draw_that_raises_still_deletes_its_workspace(honcho, monkeypatch) -> None:
    """The draw fed a message (so its workspace holds an ACTIVE session,
    which Honcho answers 409 to on a workspace delete) and then raised. The
    workspace is still deleted, sessions first, and the error still reaches
    the caller."""

    async def drive(_subject: Any, _case: Any, *, now_ms: int) -> Outcome:
        client = HonchoClient(honcho.url, _ws(specs[-1]))
        assert await client.feed(peer_id="miles_novak", text="hi", conversation_id="g-real")
        raise RuntimeError("the turn blew up")

    specs = _faked_seams(monkeypatch, drive=drive)
    case = Case(name="blows-up", input="x", metadata={"snapshot": dict(_SHOT)})
    with pytest.raises(RuntimeError, match="the turn blew up"):
        await backbone._drive(case, _spec())

    [workspace] = [_ws(s) for s in specs]
    assert honcho.deleted == [workspace]
    session_deletes = [
        r for r in honcho.requests
        if r[0] == "DELETE" and r[2] == workspace and "/sessions/" in r[1]
    ]
    assert session_deletes, "the active session is deleted before the workspace"


async def test_a_draw_whose_workspace_cannot_be_created_does_not_run(
    honcho, monkeypatch,
) -> None:
    """Fail-closed: no workspace of its own, no draw. Otherwise composition's
    best-effort get-or-create would hide that the draw was not isolated."""
    ran: list[bool] = []

    async def drive(_subject: Any, _case: Any, *, now_ms: int) -> Outcome:
        ran.append(True)
        return Outcome(admitted=None)

    _faked_seams(monkeypatch, drive=drive)
    await honcho.close()
    case = Case(name="no-honcho", input="x", metadata={"snapshot": dict(_SHOT)})
    with pytest.raises(Exception, match=r"unreachable|did not create"):
        await backbone._drive(case, _spec())
    assert not ran


async def test_two_arms_in_parallel_never_share_a_workspace(honcho) -> None:
    """Two arms are two processes, but even the same run id and the same
    case name, concurrently, get two workspaces: the nonce is what makes a
    name unique, not the run id alone."""
    admin = HonchoWorkspaces(honcho.url, deployment=_DEPLOYMENT)
    seen: list[str] = []

    async def arm(run_id: str) -> None:
        async with draw_workspace("same-case#1", run_id=run_id, admin=admin) as draw:
            seen.append(draw.id)
            await asyncio.sleep(0.05)

    await asyncio.gather(arm("run-a"), arm("run-a"), arm("run-b"))
    assert len(set(seen)) == 3, seen
    assert sorted(honcho.deleted) == sorted(seen)


async def test_the_sweep_deletes_only_stale_marked_draw_workspaces(honcho) -> None:
    """A killed run leaves its workspace behind. The sweep removes it, and
    nothing else: not a live draw of another session, not a hand-made
    `nora-eval-...` with no marker, not the shared sibling, not production,
    not a hashed per-case workspace."""
    now = datetime(2026, 9, 24, 12, tzinfo=UTC)
    old = (now - timedelta(hours=7)).isoformat()
    young = (now - timedelta(minutes=5)).isoformat()
    marked = {DRAW_MARKER: "run-x"}
    honcho.seed("nora-eval-run-x-left-behind-abc123", created_at=old, metadata=marked)
    honcho.seed("nora-eval-run-y-live-draw-def456", created_at=young, metadata=marked)
    honcho.seed("nora-eval-handmade", created_at=old)
    honcho.seed("nora-eval", created_at=old, metadata=marked)
    honcho.seed("nora", created_at=old, metadata=marked)
    honcho.seed("nora-48b00d52c0ff", created_at=old, metadata=marked)
    for n in range(120):  # past one page: the sweep must read every page
        honcho.seed(f"other-{n}", created_at=old)

    admin = HonchoWorkspaces(honcho.url, deployment=_DEPLOYMENT)
    swept = await sweep_stale_draw_workspaces(admin=admin, now=now)

    assert swept == ("nora-eval-run-x-left-behind-abc123",)
    assert honcho.deleted == list(swept)
    for kept in ("nora-eval-run-y-live-draw-def456", "nora-eval-handmade", "nora-eval",
                 "nora", "nora-48b00d52c0ff"):
        assert kept in honcho.workspaces


@pytest.mark.parametrize("workspace", [
    "nora", "nora-eval", "nora-eval-", "nora-48b00d52c0ff", "friends-eval-x", "eval-ws",
])
async def test_the_prefix_guard_refuses_before_any_honcho_call(honcho, workspace) -> None:
    """Delete is permanent and production's workspace is one prefix away.
    A workspace outside `<deployment>-eval-` is refused BEFORE a request
    leaves the process, for create and delete alike."""
    honcho.seed(workspace, created_at=datetime.now(UTC).isoformat())
    admin = HonchoWorkspaces(honcho.url, deployment=_DEPLOYMENT)
    with pytest.raises(NotADrawWorkspaceError):
        await admin.delete(workspace)
    with pytest.raises(NotADrawWorkspaceError):
        await admin.create(workspace, metadata={})
    assert honcho.requests == []
    assert workspace in honcho.workspaces


@pytest.mark.asyncio
async def test_no_memory_call_of_a_composed_subject_escapes_the_draws_workspace(
    honcho, tmp_path: Path, pg_url, monkeypatch,
) -> None:
    """The real graph, composed for a draw, with its memory pointed at the
    fake. Every Honcho client alive afterwards is on the draw's workspace,
    and every request any memory verb makes lands under it.

    The composition root builds ONE `HonchoClient`, from
    `cfg.honcho.workspace_id` with a router of no overrides, and no caller
    passes `workspace_id=` to a verb.
    So the workspace on the config IS every read and write, and this is the
    reading that would move if a second client or an explicit workspace ever
    appeared."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    admin = HonchoWorkspaces(honcho.url, deployment=_DEPLOYMENT)
    async with draw_workspace("escape-probe#1", run_id="run-z", admin=admin) as draw:
        before = {id(o) for o in gc.get_objects() if isinstance(o, HonchoClient)}
        subject = await obtain(SubjectSpec(
            database_url=os.environ["DATABASE_URL"], workdir=tmp_path,
            overrides={"honcho.base_url": honcho.url, "honcho.workspace_id": draw.id},
        ))
        try:
            clients = [
                o for o in gc.get_objects()
                if isinstance(o, HonchoClient) and id(o) not in before
            ]
            assert clients == [subject.deps.memory], "one memory client, the subject's own"
            assert subject.cfg.honcho.workspace_id == draw.id
            memory = subject.deps.memory
            peer = subject.cfg.honcho.ai_peer
            assert await memory.feed(peer_id="miles_novak", text="hi", conversation_id="g1")
            assert await memory.feed(peer_id=peer, text="hello", conversation_id="g1")
            assert await memory.conclude(peer_id=peer, content="likes tea", target="miles_novak")
            assert (await memory.representation(
                peer_id=peer, target="miles_novak", search_query="tea",
            )).reachable
            assert (await memory.representation(peer_id=peer, search_query="self")).reachable
            await memory.chat(peer_id=peer, query="who", target="miles_novak")
            assert (await memory.search(query="tea", conversation_id="g1")).reachable
            assert (await memory.search(query="tea")).reachable
            await memory.peer_card(peer_id="miles_novak")
        finally:
            await backbone._close(subject)

        touched = [r for r in honcho.requests if r[2] is not None]
        assert len(touched) >= 12, "a positive control: the verbs above did reach the fake"
        assert {r[2] for r in touched} == {draw.id}, sorted({r[2] for r in touched})
    assert honcho.deleted == [draw.id]


async def test_the_draw_workspace_is_created_on_the_subjects_own_server(
    tmp_path: Path, monkeypatch,
) -> None:
    """`memory_endpoint()` reads the template every subject composes, with
    the same host-from-the-host rewrite `_baseline` applies, so the workspace
    a draw creates is on the server its subject talks to."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    base_url, deployment = obtain_module.memory_endpoint()
    cfg = obtain_module.build_config(SubjectSpec(
        database_url="postgresql+asyncpg://x/x", workdir=tmp_path,
    ))
    assert cfg.honcho.base_url == base_url
    assert cfg.honcho.workspace_id == f"{deployment}-eval"


