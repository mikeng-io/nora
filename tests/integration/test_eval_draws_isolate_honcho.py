"""Every eval DRAW composes against case-owned services of its own.

Draw isolation is the `IsolatedServices` each case opens — its own listeners,
its own sockets, its own Redis — not a renamed Honcho namespace. These tests
drive the real `evals.backbone._drive` against recorded or request-aware
services: the deployment's configured workspace identity and routing are
preserved verbatim on the wire, every request lands on the draw's own
boundaries, success and failure both close the owned world, and a draw whose
required service cannot answer refuses before the model is reached.
"""

from __future__ import annotations

import asyncio
import importlib
import re
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import web
from opentelemetry import trace
from strands_evals import Case

from evals import backbone
from evals.rebuilt.domain import Outcome
from evals.rebuilt.entry import ENTRY_POINTS, EntryPoint
from nora.domain.models import NormalizedMessage
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.subject import SubjectSpec, obtain
from nora.subject.environment import IsolatedServices
from nora.subject.eval_evidence import (
    EvidenceError,
    RunEvidence,
    read_events,
    validate_events,
)
from nora.subject.journey_world import memory_service
from nora.subject.memory_workspace import (
    DRAW_MARKER,
    HonchoWorkspaces,
    NotADrawWorkspaceError,
    draw_workspace,
    sweep_stale_draw_workspaces,
)
from nora.subject.recorded_services import RecordedServices
from nora.subject.sandbox import eval_host_lock
from scripts.memory_graph_spike import measured_run_budget
from tests.fakes import FakeHoncho

#: The MODULE: `nora.subject` re-exports the `obtain` function under the
#: same name, so attribute access on the package returns the function.
obtain_module = importlib.import_module("nora.subject.obtain")

_DEPLOYMENT = "nora"
_SHOT = {"platform": "signal", "conversation_id": "g1", "before_ms": 2_000_000_000_000}
NOW = _SHOT["before_ms"]
IDENTITY = '[persons.miles_novak]\nname="Miles"\n[[persons.miles_novak.signal]]\nphone="+852111"\n'
_OVERRIDES = {"platforms.signal.enabled": True, "platforms.signal.mode": "bridge"}

#: Generated policy/identity with TWO workspace bindings: unit `friends` is
#: bound through Nora workspace `67` to the Honcho workspace `nora`, unit
#: `ridgeline` to a Nora workspace with no Honcho binding (its own name).
_MULTI_POLICY = """
default = "deny"

[[conversations]]
platform = "signal"
id = "g-friends"
label = "Friends"
persons = "*"
unit = "friends"

[[conversations]]
platform = "signal"
id = "g-ridgeline"
label = "Ridgeline"
persons = "*"
unit = "ridgeline"

[units.friends]
workspace = "67"

[units.ridgeline]
workspace = "ridgeline"
"""

_MULTI_IDENTITY = (
    '[persons.miles_novak]\nname="Miles"\n[[persons.miles_novak.signal]]\nphone="+852111"\n'
    '[workspaces.67]\nhoncho_workspace = "nora"\n'
)


@pytest.fixture
async def honcho(monkeypatch):
    """A fake Honcho, and the subject side pointed at it: `memory_endpoint()`
    is where every draw workspace's server and prefix come from."""
    fake = FakeHoncho()
    url = await fake.start()
    monkeypatch.setattr(obtain_module, "memory_endpoint", lambda: (url, _DEPLOYMENT))
    yield fake
    await fake.close()


@pytest.fixture
def run_evidence(tmp_path):
    """An active `RunEvidence` — `_drive` refuses without canonical capture."""
    backbone._prefill_capture()
    budget = {}
    with eval_host_lock() as lock_fd:
        evidence = RunEvidence(
            run_id="generated-draws", lock_fd=lock_fd,
            private_root=tmp_path / "capture", metadata={"behavior": "UNMEASURED"})
        try:
            with evidence.capture(trace.get_tracer_provider()), measured_run_budget(
                SimpleNamespace(stop_after_tokens=20000), evidence, tmp_path, budget,
                max_requests=20, max_output_tokens=400000,
            ):
                yield evidence
        except EvidenceError as exc:
            assert str(exc) == "incomplete_record"
            events = read_events(evidence.path)
            assert any(e["kind"] == "expected_invalid_capture" for e in events)
            assert validate_events(events)["causes"] == ["capture_interrupted"]
        else:
            assert validate_events(read_events(evidence.path))["status"] == "PASS"


def _recording(cfg, *, workspace_create: bool = True, completion: bool = True):
    """Finite external responses for one case's owned world, in protocol order."""
    bindings = {"llm.base_url": "model", "honcho.base_url": "memory",
                "platforms.signal.base_url": "bridge"}
    bindings.update({f"providers.{name}.base_url": "model" for name in cfg.providers})
    model = cfg.endpoint("relevance_gate", cfg.relevance_gate.model)[2]
    memory = [
        {"method": "POST", "path": "/v3/workspaces", "request_json": {
            "id": cfg.honcho.workspace_id}, "response": {"status": 200, "json": {
                "id": cfg.honcho.workspace_id}}},
        {"method": "POST", "path": f"/v3/workspaces/{cfg.honcho.workspace_id}/peers",
         "request_json": {"id": cfg.honcho.ai_peer}, "response": {"status": 200,
             "json": {"id": cfg.honcho.ai_peer}}},
    ]
    if not workspace_create:
        memory = memory[1:]
    model_rows = [{
        "method": "POST", "path": "/v1/chat/completions", "response": {"status": 200, "json": {
            "id": "generated-completion", "model": model, "created": 1,
            "object": "chat.completion", "choices": [{"index": 0,
                "message": {"role": "assistant", "content": '{"verdict":"go"}'},
                "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        }},
    }] if completion else []
    return RecordedServices({"version": 1, "bindings": bindings, "services": {
        "bridge": [{"method": "GET", "path": "/v1/self", "response": {"status": 200,
                     "json": {"primary": "generated-self", "aliases": []}}}],
        "memory": memory,
        "model": model_rows,
    }})


def _watch(monkeypatch):
    """`IsolatedServices._coverage` wrapped to record (env, service, method,
    path, json) per request — the wire-traffic instrument."""
    received: list[tuple[Any, str, str, str, Any]] = []
    coverage = IsolatedServices._coverage

    def observe(self, service):
        original = coverage(self, service)

        @web.middleware
        async def capture(request, handler):
            body = None
            if request.method == "POST":
                try:
                    body = await request.json()
                except (ValueError, UnicodeDecodeError):
                    body = None
            received.append((self, service, request.method, request.raw_path, body))
            return await original(request, handler)

        return capture

    monkeypatch.setattr(IsolatedServices, "_coverage", observe)
    return received


async def _seed(clean_pg_url):
    engine = create_sql_engine(clean_pg_url)
    try:
        await SqlRecordStore(engine).append(message=NormalizedMessage(
            "signal", "g1", "group", "+852111", "Miles", NOW - 1000,
            "Generated earlier conversation", {}), person_id="miles_novak")
    finally:
        await engine.dispose()


async def test_each_draw_composes_against_its_own_case_owned_services(
    clean_pg_url, evaluation_config, tmp_path, monkeypatch, run_evidence,
) -> None:
    """Three draws of one case: three case-owned service environments, the
    deployment's configured workspace created on each draw's OWN listener
    before any memory traffic, every request inside the draw's owned world,
    and every environment closed with nothing carried into the next draw."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated-token")
    await _seed(clean_pg_url)
    cfg = obtain_module.build_config(SubjectSpec(
        database_url=clean_pg_url, workdir=tmp_path / "declared", overrides=_OVERRIDES))
    received = _watch(monkeypatch)
    subjects: list[Any] = []
    real_obtain = backbone.obtain

    async def compose(spec: Any) -> Any:
        subject = await real_obtain(spec)
        subjects.append(subject)
        return subject

    monkeypatch.setattr(backbone, "obtain", compose)
    cases = [Case(name=f"generated-{index}", input="Generated question", metadata={
        "expect": "go", "snapshot": dict(_SHOT)}) for index in range(3)]
    spec = backbone.Spec(
        source_url=clean_pg_url, entry="gate", identity=IDENTITY,
        policy=obtain_module.default_policy(("signal", "g1")), overrides=_OVERRIDES,
        services={case.name: _recording(cfg) for case in cases})
    results = [await backbone._drive(case, spec) for case in cases]

    assert len(subjects) == 3
    environments = [subject.environment for subject in subjects]
    assert len({id(env) for env in environments}) == 3, (
        "each draw's services are its own — nothing shared between draws"
    )
    assert all(
        subject.cfg.honcho.workspace_id == cfg.honcho.workspace_id
        for subject in subjects
    ), "the deployment's workspace identity is preserved, not renamed"
    for env in environments:
        calls = [row for row in received if row[0] is env]
        memory_calls = [row for row in calls if row[1] == "honcho.base_url"]
        assert memory_calls, "the draw's memory traffic stayed on its own listener"
        assert memory_calls[0][2:4] == ("POST", "/v3/workspaces") and (
            memory_calls[0][4] or {}).get("id") == cfg.honcho.workspace_id, (
            "the workspace is created on the draw's own service, first, under "
            "the deployment's configured name"
        )
        assert not env.active
        assert not env.denied, f"requests outside the draw's owned world: {env.denied}"
    assert all(not subject.deps.background._tasks for subject in subjects)
    assert not getattr(socket.socket.connect, "_nora_eval_guard", False)
    errors = [
        {e.name: e.state for e in result["environment_state"]}[backbone.NORA_ERROR]
        for result in results
    ]
    assert errors == [None, None, None], errors


async def test_a_draw_whose_drive_fails_still_closes_its_case_owned_services(
    clean_pg_url, evaluation_config, tmp_path, monkeypatch, run_evidence,
) -> None:
    """The drive raises after composition — real workspace traffic already on
    the owned listener — and the draw's whole owned world is still closed,
    subjects first, the error reaching the caller."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated-token")
    await _seed(clean_pg_url)
    cfg = obtain_module.build_config(SubjectSpec(
        database_url=clean_pg_url, workdir=tmp_path / "declared", overrides=_OVERRIDES))
    received = _watch(monkeypatch)
    subjects: list[Any] = []
    real_obtain = backbone.obtain

    async def compose(spec: Any) -> Any:
        subject = await real_obtain(spec)
        subjects.append(subject)
        return subject

    async def drive(_subject: Any, _case: Any, *, now_ms: int) -> Outcome:
        raise RuntimeError("the turn blew up")

    monkeypatch.setattr(backbone, "obtain", compose)
    monkeypatch.setitem(ENTRY_POINTS, "dreamer", EntryPoint(drive=drive))
    case = Case(name="blows-up", input="x", metadata={"snapshot": dict(_SHOT)})
    spec = backbone.Spec(
        source_url=clean_pg_url, entry="dreamer", identity=IDENTITY,
        policy=obtain_module.default_policy(("signal", "g1")), overrides=_OVERRIDES,
        services={case.name: _recording(cfg, completion=False)})
    with pytest.raises(RuntimeError, match="the turn blew up"):
        await backbone._drive(case, spec)

    [env] = [subject.environment for subject in subjects]
    creates = [row for row in received
               if row[0] is env and row[2:4] == ("POST", "/v3/workspaces")]
    assert creates, "composition's workspace traffic did reach the owned service"
    assert not env.active, "the draw's owned world is closed even on error"
    assert not getattr(socket.socket.connect, "_nora_eval_guard", False)
    run_evidence.record("expected_invalid_capture")


async def test_a_draw_whose_service_cannot_answer_refuses_before_the_drive(
    clean_pg_url, evaluation_config, tmp_path, monkeypatch, run_evidence,
) -> None:
    """Fail-closed: the recording holds no answer for the workspace create, so
    composition is refused at the boundary and the drive never runs — the
    refused call is recorded as denied evidence, not hidden."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated-token")
    await _seed(clean_pg_url)
    cfg = obtain_module.build_config(SubjectSpec(
        database_url=clean_pg_url, workdir=tmp_path / "declared", overrides=_OVERRIDES))
    _watch(monkeypatch)
    ran: list[bool] = []
    environments: list[IsolatedServices] = []

    async def drive(_subject: Any, _case: Any, *, now_ms: int) -> Outcome:
        ran.append(True)
        return Outcome(admitted=None)

    monkeypatch.setitem(ENTRY_POINTS, "dreamer", EntryPoint(drive=drive))
    recording = _recording(cfg, workspace_create=False, completion=False)
    real_open = recording.open

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def spy(*args: Any, **kwargs: Any):
        async with real_open(*args, **kwargs) as env:
            environments.append(env)
            yield env

    monkeypatch.setattr(recording, "open", spy)
    case = Case(name="no-honcho", input="x", metadata={"snapshot": dict(_SHOT)})
    spec = backbone.Spec(
        source_url=clean_pg_url, entry="dreamer", identity=IDENTITY,
        policy=obtain_module.default_policy(("signal", "g1")), overrides=_OVERRIDES,
        services={case.name: recording})
    with pytest.raises(EvidenceError, match="external_response_not_recorded"):
        await backbone._drive(case, spec)

    assert not ran, "a draw whose owned service cannot answer must not run"
    [env] = environments
    assert any(row["cause"] == "external_response_not_recorded" for row in env.denied), (
        "the refusal is recorded as denied evidence, not swallowed"
    )
    assert not env.active
    run_evidence.record("expected_invalid_capture")


async def test_no_memory_call_of_a_composed_subject_escapes_its_case_owned_world(
    clean_pg_url, evaluation_config, tmp_path, monkeypatch,
) -> None:
    """The real graph, composed for a case, against generated policy/identity
    carrying TWO workspace bindings. Every workspace the router names is
    created on the case's OWN listener; a feed to each room lands under its
    bound workspace — routing preserved, nothing reaching outside the owned
    world, and the deployment's default workspace kept as configured."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated-token")
    feeds: list[Any] = []
    received = _watch(monkeypatch)
    bridge = web.Application()

    async def self_rpc(request):
        return web.json_response({"primary": "generated-self", "aliases": []})

    bridge.router.add_get("/v1/self", self_rpc)
    applications = {
        "llm.base_url": web.Application(),
        "honcho.base_url": memory_service(feeds),
        "platforms.signal.base_url": bridge,
    }
    cfg = obtain_module.build_config(SubjectSpec(
        database_url=clean_pg_url, workdir=tmp_path / "declared",
        identity=_MULTI_IDENTITY, policy=_MULTI_POLICY,
        room=("signal", "g-friends"), overrides=_OVERRIDES))
    for name in cfg.providers:
        applications[f"providers.{name}.base_url"] = web.Application()
    async with IsolatedServices(
        database_url=clean_pg_url, applications=applications, redis=True,
    ) as env:
        subject = await obtain(SubjectSpec(
            database_url=clean_pg_url, workdir=tmp_path,
            environment=env, identity=_MULTI_IDENTITY, policy=_MULTI_POLICY,
            room=("signal", "g-friends"), overrides=_OVERRIDES))
        memory = subject.deps.memory
        peer = subject.cfg.honcho.ai_peer
        assert subject.cfg.honcho.workspace_id == "nora", (
            "the deployment's configured workspace is preserved — isolation "
            "is the owned service, not a renamed namespace"
        )
        assert await memory.feed(
            peer_id="miles_novak", text="hi", conversation_id="g-friends")
        assert await memory.feed(
            peer_id="miles_novak", text="hi again", conversation_id="g-ridgeline")
        await memory.peer_card(peer_id="miles_novak", conversation_id="g-friends")
        assert (await memory.representation(
            peer_id=peer, target="miles_novak", search_query="tea",
            conversation_id="g-friends")).reachable
        unrouted = await memory.peer_card(peer_id="miles_novak")
        assert unrouted == [], (
            "a call naming no room or unit fails closed under declared units — "
            "never widened to the default workspace"
        )
        calls = [row for row in received if row[0] is env]
        memory_calls = [row for row in calls if row[1] == "honcho.base_url"]
        created = {
            (row[4] or {}).get("id") for row in memory_calls
            if row[2:4] == ("POST", "/v3/workspaces")
        }
        assert {"nora", "ridgeline"} <= created, (
            f"every bound workspace is created on the case's own service: {created}"
        )
        routed = {
            match.group(1)
            for _, _, _, path, _ in memory_calls
            if (match := re.match(r"/v3/workspaces/([^/]+)/", path))
        } - {"list"}
        assert routed == {"nora", "ridgeline"}, (
            f"each room's memory traffic lands under its own bound workspace: {routed}"
        )
        assert not env.denied, f"calls outside the owned world: {env.denied}"
    assert not env.active
    assert feeds, "a positive control: the feed calls really reached the service"


async def test_the_subject_keeps_the_deployments_workspace_identity(
    tmp_path: Path, monkeypatch,
) -> None:
    """`memory_endpoint()` reads the template every subject composes: the
    subject keeps the deployment's configured workspace name — the draw's
    isolation is the case-owned server it binds to, not a renamed workspace."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    base_url, deployment = obtain_module.memory_endpoint()
    cfg = obtain_module.build_config(SubjectSpec(
        database_url="postgresql+asyncpg://x/x", workdir=tmp_path,
    ))
    assert cfg.honcho.base_url == base_url
    assert cfg.honcho.workspace_id == deployment


class _Deps:
    class _Closable:
        async def cancel_all(self) -> None: ...
        async def aclose(self) -> None: ...
        async def close(self) -> None: ...

    background = audit = client = _Closable()
    doctrine = SimpleNamespace(doctrine_text="doctrine")


async def test_a_draw_workspace_is_created_with_derivation_off(honcho) -> None:
    """A draw can never read what the deriver derives from it (derivation
    lags tens of minutes), so the create asks Honcho for no reasoning and no
    summaries: no queue load on the Honcho production's memory runs on."""
    admin = HonchoWorkspaces(honcho.url, deployment=_DEPLOYMENT)
    async with draw_workspace("derive-probe#1", run_id="run-d", admin=admin) as draw:
        configuration = honcho.workspaces[draw.id]["configuration"]
    assert configuration == {"reasoning": {"enabled": False}, "summary": {"enabled": False}}


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
