"""Real HTTP/Redis protocols at the case-owned evaluation boundary."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import shlex
import socket
import sys
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import aiohttp
import httpx
import httpx2
import pytest
import redis.asyncio as redis
from aiohttp import web

from nora.app.composition import Dependencies
from nora.domain.actions import ActionOwner
from nora.domain.models import ConversationTarget
from nora.platform.bridge.client import BridgeAdapter, BridgeEndpoint, BridgeUnavailableError
from nora.subject import SubjectSpec, obtain
from nora.subject.environment import IsolatedServices
from nora.subject.eval_evidence import EvidenceError
from nora.subject.obtain import build_config, default_policy
from nora.testing.signal_daemon import FakeSignalDaemon
from scripts.cognition_loop.egress import assert_no_egress


def _bridge_app(received: list[dict]) -> web.Application:
    app = web.Application()

    async def identity(request: web.Request) -> web.Response:
        return web.json_response({"primary": "generated-self", "aliases": ["generated-alias"]})

    async def action(request: web.Request) -> web.Response:
        body = await request.json()
        if body.get("op") != "send" or not body.get("request", {}).get("text"):
            return web.json_response({"cause": "wrong_action"}, status=422)
        received.append(body)
        state = "rejected" if body["request"]["text"] == "reject" else "confirmed"
        return web.json_response({"state": state, "platform_ref": f"generated-{len(received)}"})

    async def groups(request: web.Request) -> web.Response:
        return web.json_response([{"id": "g1", "members": []}])

    async def absent(request: web.Request) -> web.Response:
        return web.json_response({"error": "known_missing"}, status=404)

    app.router.add_get("/v1/self", identity)
    app.router.add_post("/v1/actions", action)
    app.router.add_get("/v1/groups", groups)
    app.router.add_get("/known-absent", absent)
    return app


@pytest.mark.parametrize("members,available", [
    ([["generated-a", "generated-alias"], ["generated-b"]], True),
    ([], True), (None, False), (["generated-a"], False), ([[]], False), ([[False]], False),
    ("invalid-json", False), ("invalid-root", False),
])
async def test_bridge_roster_decodes_accounts_once_and_keeps_unknown_unavailable(
    members, available,
):
    from nora.platform.bridge.client import BridgeRoster

    application = web.Application()

    async def groups(request):
        if members == "invalid-json":
            return web.Response(text="{", content_type="application/json")
        if members == "invalid-root":
            return web.json_response(None)
        return web.json_response([{"id": "generated-room", "members": members}])

    application.router.add_get("/v1/groups", groups)
    async with IsolatedServices(database_url="sqlite:///generated", applications={
        "platforms.signal.base_url": application,
    }) as environment:
        roster = BridgeRoster("signal", BridgeEndpoint(
            environment.urls["platforms.signal.base_url"], "generated-token"))
        known = await roster.roster(conversation_id="generated-room")
        unknown = await roster.roster(conversation_id="generated-unlisted")
        assert known.available is available
        assert not unknown.available
        if available:
            assert known.members == tuple(frozenset(account) for account in members)
            assert all(isinstance(account, frozenset) for account in known.members)
            if members:
                assert known.members[0] & frozenset({"generated-a"}) == {"generated-a"}
        else:
            assert known.members == ()
        environment.check()


@pytest.fixture
def spec(tmp_path, clean_pg_url, monkeypatch, evaluation_config) -> SubjectSpec:
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    # No identity.toml or policy.toml written here, deliberately: `obtain` owns
    # them, because a caller writing them by hand is how every caller ends up
    # with a slightly different Nora.
    return SubjectSpec(
        database_url=os.environ["DATABASE_URL"], workdir=tmp_path,
        room=("signal", "g1"),
        policy=default_policy(("signal", "g1"), unit="generated-unit"))


@pytest.mark.asyncio
async def test_obtain_returns_the_production_graph(spec, clean_pg_url) -> None:
    """Same type, same builder, same field count as `nora/__main__.py` gets."""
    requests: list[dict] = []
    daemon = FakeSignalDaemon()
    applications = {
        "platforms.signal.base_url": daemon.app(),
        "honcho.base_url": _empty_honcho_app(),
        "llm.base_url": _model_app(requests),
        **{f"providers.{name}.base_url": _model_app(requests)
           for name in build_config(spec).providers},
    }
    environment = IsolatedServices(
        database_url=clean_pg_url, applications=applications)
    async with environment:
        subject = await obtain(replace(spec, environment=environment))
    assert isinstance(subject.deps, Dependencies)
    fields = {f.name for f in dataclasses.fields(Dependencies)}
    assert len(fields) == 85, "Dependency shape changed; reconcile the production subject."
    assert {"access", "managed_services", "managed_session"} <= fields
    assert subject.deps.access is subject.deps.ingest._access


@pytest.mark.asyncio
async def test_real_bridge_identity_bound_sends_and_refusals_stay_in_owned_service():
    received: list[dict] = []
    application = _bridge_app(received)
    environment = IsolatedServices(database_url="sqlite:///generated",
        applications={"platforms.signal.base_url": application,
                      "platforms.discord.base_url": application})
    async with environment:
        assert (environment.urls["platforms.signal.base_url"]
                == environment.urls["platforms.discord.base_url"])
        adapter = BridgeAdapter("signal", BridgeEndpoint(
            environment.urls["platforms.signal.base_url"], "generated-token"))
        try:
            identity = await adapter.resolve_identity()
            assert identity.primary == "generated-self"
            bound = adapter.bind_owner(ActionOwner(kind="work", id="generated-work", generation=1))
            target = ConversationTarget(platform="signal", conversation_id="generated-room",
                                        conversation_type="group")
            accepted = await bound.send(target=target, text="accept")
            refused = await bound.send(target=target, text="reject")
            assert accepted.ok and accepted.platform_message_id == "generated-1"
            assert not refused.ok
            assert [row["request"]["text"] for row in received] == ["accept", "reject"]
            assert received[0]["owner"] == {"kind": "work", "id": "generated-work", "generation": 1}
            assert received[0]["action_id"] != received[1]["action_id"]
            subject = SimpleNamespace(
                environment=environment, database_url=environment.database_url,
                deps=SimpleNamespace(fire_path=object(), bridge_runtime=object()))
            assert assert_no_egress(subject)["workers"]["fire_path"]
            assert not environment.denied
            with pytest.raises(BridgeUnavailableError, match="404"):
                await adapter._get("/known-absent")
            environment.check()
            with pytest.raises(BridgeUnavailableError, match="404"):
                await adapter._get("/unrecorded-read")
            with pytest.raises(EvidenceError, match="external_response_not_recorded"):
                environment.check()
        finally:
            await adapter.close()
    assert not environment.active


@pytest.mark.asyncio
async def test_database_alias_uses_only_addresses_resolved_before_isolation(monkeypatch):
    original = socket.getaddrinfo
    lookups = []

    def resolver(host, port, *args, **kwargs):
        if host == "generated-db.invalid":
            lookups.append(host)
            return original("127.0.0.1", port, *args, **kwargs)
        return original(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    async with IsolatedServices(
        database_url="postgresql://generated-db.invalid:5432/generated", applications={}
    ) as environment:
        addresses = socket.getaddrinfo("generated-db.invalid", 5432, type=socket.SOCK_STREAM)
        assert {row[4][0] for row in addresses} == {"127.0.0.1"}
        assert lookups == ["generated-db.invalid"]
        with pytest.raises(PermissionError):
            socket.getaddrinfo("generated-db.invalid", 5433)
        with pytest.raises(PermissionError):
            socket.getaddrinfo("other.invalid", 5432)
        assert len(environment.denied) == 2


@pytest.mark.asyncio
async def test_unknown_tcp_and_dns_are_refused_and_cannot_be_a_valid_zero():
    original = socket.socket.connect
    async with IsolatedServices(database_url="sqlite:///generated", applications={}) as environment:
        with pytest.raises(PermissionError, match="not isolated"):
            await asyncio.open_connection("127.0.0.1", 9)
        with pytest.raises(PermissionError, match="not isolated"):
            socket.getaddrinfo("unrecorded.example.invalid", 443)
        with pytest.raises(PermissionError, match="not isolated"):
            await asyncio.create_subprocess_exec(sys.executable, "-c", "raise SystemExit(0)")
        assert {row["operation"] for row in environment.denied} == {
            "connect", "resolve", "subprocess"}
        with pytest.raises(EvidenceError, match="external_dependency_not_isolated"):
            environment.check()
    assert socket.socket.connect is original


@pytest.mark.asyncio
async def test_the_same_real_child_process_cannot_spawn_inside_the_boundary(tmp_path):
    """The asyncio spawn doors share `subprocess.Popen` on the supported loop."""
    exec_sentinel = tmp_path / "exec.sentinel"
    shell_sentinel = tmp_path / "shell.sentinel"
    child = "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('spawned')"

    async def spawn_exec(path) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            sys.executable, "-c", child, str(path))

    async def spawn_shell(path) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_shell(
            f"echo spawned > {shlex.quote(str(path))}")

    assert await (await spawn_exec(exec_sentinel)).wait() == 0
    assert await (await spawn_shell(shell_sentinel)).wait() == 0
    assert exec_sentinel.read_text() == "spawned"
    assert shell_sentinel.read_text().strip() == "spawned"
    exec_sentinel.unlink()
    shell_sentinel.unlink()

    async with IsolatedServices(database_url="sqlite:///generated",
                                applications={}) as environment:
        with pytest.raises(PermissionError, match="not isolated"):
            await spawn_exec(exec_sentinel)
        with pytest.raises(PermissionError, match="not isolated"):
            await spawn_shell(shell_sentinel)
        assert not exec_sentinel.exists()
        assert not shell_sentinel.exists()
        assert [row["operation"] for row in environment.denied] == ["subprocess"] * 2
        with pytest.raises(EvidenceError, match="external_dependency_not_isolated"):
            environment.check()

    assert await (await spawn_exec(exec_sentinel)).wait() == 0
    assert exec_sentinel.read_text() == "spawned"


@pytest.mark.asyncio
@pytest.mark.parametrize("http", [httpx, httpx2], ids=["httpx", "httpx2"])
async def test_http_dispatch_and_redirects_cannot_bypass_owned_services(http):
    received: list[dict] = []
    unowned = web.AppRunner(_bridge_app(received), access_log=None)
    await unowned.setup()
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.setblocking(False)
    url = f"http://127.0.0.1:{listener.getsockname()[1]}"
    await web.SockSite(unowned, listener).start()
    try:
        # Show the unowned listener is available before testing its exclusion.
        async with aiohttp.ClientSession() as client, client.get(f"{url}/v1/self") as response:
            assert response.status == 200
        app = _bridge_app([])

        async def redirect(request):
            raise web.HTTPFound(f"{url}/v1/self")

        app.router.add_get("/redirect", redirect)
        async with IsolatedServices(database_url="sqlite:///generated",
                applications={"platforms.signal.base_url": app}) as environment:
            owned = environment.urls["platforms.signal.base_url"]
            async with http.AsyncClient(follow_redirects=True) as client:
                response = await client.get(f"{owned}/v1/self")
                assert response.json()["primary"] == "generated-self"
                for target in (f"{url}/v1/actions", f"{owned}/redirect"):
                    refusal = None
                    try:
                        await client.get(target)
                    except (PermissionError, http.ConnectError) as exc:
                        refusal = exc
                    assert isinstance(refusal, PermissionError), (
                        "the HTTP dispatch reached transport instead of its isolation refusal: "
                        f"{type(refusal).__name__}")
            with http.Client() as client, pytest.raises(PermissionError, match="not isolated"):
                client.post(f"{url}/v1/actions", json={"op": "send", "request": {"text": "leak"}})
            assert [row["operation"] for row in environment.denied] == ["http"] * 3
            assert received == []
            with pytest.raises(EvidenceError, match="external_dependency_not_isolated"):
                environment.check()
    finally:
        await unowned.cleanup()


@pytest.mark.asyncio
async def test_failed_case_cleans_listeners_and_cancels_its_spawned_work():
    original = socket.socket.connect
    started, stopped = asyncio.Event(), asyncio.Event()

    async def worker():
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            stopped.set()

    environment = IsolatedServices(database_url="sqlite:///generated",
        applications={"platforms.signal.base_url": _bridge_app([])})
    with pytest.raises(ValueError, match="generated failure"):
        async with environment:
            task = asyncio.create_task(worker())
            await started.wait()
            url = environment.urls["platforms.signal.base_url"]
            async with aiohttp.ClientSession() as client, client.get(f"{url}/v1/self") as response:
                assert response.status == 200
            raise ValueError("generated failure")
    assert task.cancelled() and stopped.is_set()
    assert socket.socket.connect is original
    async with aiohttp.ClientSession() as client:
        with pytest.raises(aiohttp.ClientConnectorError):
            await client.get(f"{url}/v1/self")


@pytest.mark.asyncio
async def test_an_overlapping_case_refuses_without_removing_the_first_guard():
    async with IsolatedServices(database_url="sqlite:///generated", applications={}) as first:
        with pytest.raises(RuntimeError, match="share a network context"):
            async with IsolatedServices(database_url="sqlite:///other", applications={}):
                pytest.fail("overlapping cases were admitted")
        first.check()


@pytest.mark.asyncio
async def test_bridge_redis_is_a_private_unix_service_and_is_removed_on_exit():
    environment = IsolatedServices(database_url="sqlite:///generated", applications={}, redis=True)
    async with environment:
        client = redis.from_url(environment.redis_url)
        try:
            assert await client.ping()
            await client.set("generated-key", "generated-value")
            assert await client.get("generated-key") == b"generated-value"
        finally:
            await client.aclose()
        # The owned spawn preceded the guard; the guard is still up beside it.
        assert environment.guarded
        with pytest.raises(PermissionError, match="not isolated"):
            await asyncio.create_subprocess_exec(sys.executable, "-c", "pass")
        assert [row["operation"] for row in environment.denied] == ["subprocess"]
        process = environment._process
        directory = environment._directory.name
    from pathlib import Path

    assert process.returncode is not None
    assert not Path(directory).exists()  # noqa: ASYNC240 -- local teardown readback


@pytest.mark.asyncio
async def test_cancellation_closes_the_case_before_restoring_network_access():
    entered, stopped = asyncio.Event(), asyncio.Event()
    environment = IsolatedServices(database_url="sqlite:///generated",
        applications={"platforms.signal.base_url": _bridge_app([])})
    original = socket.socket.connect

    async def case():
        async with environment:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

    task = asyncio.create_task(case())
    await entered.wait()
    assert environment.guarded
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()
    assert not environment.active
    assert socket.socket.connect is original


def _empty_honcho_app() -> web.Application:
    app = web.Application()

    async def create(request: web.Request) -> web.Response:
        body = await request.json()
        if not isinstance(body.get("id"), str):
            return web.json_response({"cause": "id_required"}, status=422)
        return web.json_response(body)

    async def empty(request: web.Request) -> web.Response:
        return web.json_response({"items": [], "total": 0})

    app.router.add_post("/v3/workspaces", create)
    app.router.add_post("/v3/workspaces/{workspace}/peers", create)
    app.router.add_post("/v3/workspaces/{workspace}/scopes", create)
    app.router.add_post("/v3/workspaces/{workspace}/scopes/{scope}/sessions/list", empty)
    return app


def _model_app(requests: list[dict]) -> web.Application:
    app = web.Application()

    async def completion(request: web.Request) -> web.StreamResponse:
        body = await request.json()
        if not body.get("model") or not body.get("messages") or not body.get("tools"):
            return web.json_response({"error": "incomplete model request"}, status=422)
        requests.append(body)
        response = {"id": f"generated-{len(requests)}", "model": body["model"],
                    "created": 1, "object": "chat.completion",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": "Done"},
                                 "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11}}
        if not body.get("stream"):
            return web.json_response(response)
        stream = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await stream.prepare(request)
        for delta, reason in (({"role": "assistant", "content": "Done"}, None), ({}, "stop")):
            chunk = {**response, "object": "chat.completion.chunk",
                     "choices": [{"index": 0, "delta": delta, "finish_reason": reason}]}
            await stream.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await stream.write(b"data: [DONE]\n\n")
        await stream.write_eof()
        return stream

    app.router.add_post("/v1/chat/completions", completion)
    app.router.add_post("/chat/completions", completion)
    return app


@pytest.mark.asyncio
async def test_actual_production_composition_retains_fire_bridge_and_cognition(
    clean_pg_url, evaluation_config, tmp_path, monkeypatch,
):
    from nora.app.composition import Dependencies, build_dependencies, wall_clock
    from nora.subject import SubjectSpec, obtain
    from nora.subject.obtain import build_config, default_policy

    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated-token")
    applications = {f"platforms.{platform}.base_url": _bridge_app([])
                    for platform in ("signal", "discord", "whatsapp", "instagram")}
    applications["honcho.base_url"] = _empty_honcho_app()
    overrides = {f"platforms.{platform}.{key}": value
                 for platform in ("signal", "discord", "whatsapp", "instagram")
                 for key, value in (("enabled", True), ("mode", "bridge"))}
    overrides.update({f"{name}.enabled": True for name in (
        "cognition", "heartbeat", "compute", "participation", "relevance_gate", "presence")})
    overrides["memory.index.embed"] = True
    now = datetime(2026, 1, 1, tzinfo=UTC)
    spec = SubjectSpec(database_url=clean_pg_url, workdir=tmp_path,
                       overrides=overrides, now=now,
                       policy=default_policy(("signal", "g1")) + "home_room = true\n")
    declared = build_config(spec)
    requests: list[dict] = []
    if declared.llm.base_url:
        applications["llm.base_url"] = _model_app(requests)
    for provider in declared.providers:
        applications[f"providers.{provider}.base_url"] = _model_app(requests)
    environment = IsolatedServices(database_url=clean_pg_url,
                                    applications=applications, redis=True)
    async with environment:
        subject = await obtain(replace(spec, environment=environment))
        assert isinstance(subject.deps, Dependencies)
        assert subject.deps.fire_path is not None
        assert subject.deps.bridge_runtime is not None
        assert subject.deps.cognition_runner is not None
        assert subject.deps.memory_embed is not None
        assert set(subject.deps.registry.adapters) == {"signal", "discord", "whatsapp", "instagram"}
        assert all(getattr(subject.cfg.platforms, p).mode == "bridge"
                   for p in subject.deps.registry.adapters)
        assert all(getattr(subject.cfg, name).enabled for name in (
            "heartbeat", "compute", "participation", "relevance_gate", "presence"))
        assert assert_no_egress(subject)["isolated"]
        assert not environment.denied
        # The control uses production's composition root directly. Compare
        # actual SDK wire bodies, not an inspection/pre-assembly substitute.
        control_cfg = environment.bind_config(build_config(spec))
        control_deps = await build_dependencies(control_cfg, clock=lambda: now,
                                                operational_clock=wall_clock)
        control = replace(subject, cfg=control_cfg, deps=control_deps)
        environment.register_subject(control)
        assert control.deps.memory_embed is not None
        assert type(control.deps.memory_embed) is type(subject.deps.memory_embed)
        assert control_cfg.store.database_url_env != subject.cfg.store.database_url_env
        assert subject.cfg.credentials[subject.cfg.store.database_url_env] == clean_pg_url
        for graph in (control, subject):
            result = await graph.deps.cognition_runner.probe(
                unit=subject.unit, summary="Generated empty affair",
                now_ms=int(now.timestamp() * 1000))
            assert result.stop_reason == "end_turn"
        assert len(requests) == 2
        assert requests[0] == requests[1]
        assert not environment.denied
        await control.aclose()
        await subject.aclose()
        assert not subject.deps.background._tasks
    assert not environment.active
