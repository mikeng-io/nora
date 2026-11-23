"""Cold production acquisition without App startup or model responses."""

import asyncio
import base64
import json
import os
import socket
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path

import boto3
import pytest
from aiohttp import web
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from sqlalchemy import text

from nora.app.computer_queue import SegmentRegistry
from nora.app.startup import StartupError
from nora.infra.blobs import RustfsBlobStore
from nora.platform.bridge.client import ACCOUNT_PATH, QUERIES_PATH
from nora.platform.bridge.contract import read_body
from nora.subject.environment import IsolatedServices
from nora.subject.obtain import SubjectSpec, build_config, default_policy, obtain


def credential_names(data):
    for key, value in data.items():
        if isinstance(value, dict):
            yield from credential_names(value)
        elif key.endswith("_env") and value:
            yield value


def boundary_apps(cfg, received, payloads):
    apps = {}
    model = web.Application()

    async def no_model(request):
        received.append("MODEL_DISPATCH")
        raise AssertionError("composition must not dispatch a model")

    model.router.add_post("/v1/chat/completions", no_model)
    apps["llm.base_url"] = model
    apps.update({f"providers.{name}.base_url": model for name in cfg.providers})
    for name in ("signal", "discord", "whatsapp", "instagram"):
        app = web.Application()

        async def read(request, platform=name):
            received.append(f"{platform}:{request.path}")
            return web.json_response(read_body("ok", {
                ACCOUNT_PATH: {"primary": f"generated-{platform}", "aliases": []},
                "/v1/operations/status": {"ok": True},
            }[request.path], source=platform, trace=""))

        async def reads(request, platform=name):
            body = await request.json()
            assert body["kind"] in {"channels", "groups"}, body
            received.append(f"{platform}:{body['kind']}")
            return web.json_response(read_body("ok", [], source=platform, trace=""))

        for path in (ACCOUNT_PATH, "/v1/operations/status"):
            app.router.add_get(path, read)
        app.router.add_post(QUERIES_PATH, reads)
        apps[f"platforms.{name}.base_url"] = app
    s3 = web.Application()

    async def bucket(request):
        assert request.method == "HEAD" and request.path == f"/{cfg.media.rustfs.bucket}"
        assert request.headers.get("Authorization", "").startswith("AWS4-HMAC-SHA256 ")
        received.append("rustfs:head_bucket")
        return web.Response()

    s3.router.add_head(f"/{cfg.media.rustfs.bucket}", bucket)
    apps["media.rustfs.endpoint"] = s3
    telemetry = web.Application()

    async def collect(request):
        payloads.append(ExportTraceServiceRequest.FromString(await request.read()))
        return web.Response()

    telemetry.router.add_post("/v1/traces", collect)
    apps["obs.tracing.endpoint"] = telemetry
    return apps


@pytest.mark.fresh_database
@pytest.mark.parametrize("exit_mode", ["success", "case_error", "startup_error", "missing_token"])
async def test_cold_composition_preserves_features_and_closes_owned_resources(
    clean_pg_url, evaluation_config, tmp_path, monkeypatch, exit_mode,
):
    source = Path(os.environ.get("NORA_COMPOSITION_CONFIG", evaluation_config))
    spec = preflight_spec(source, evaluation_config, clean_pg_url, tmp_path, exit_mode)
    await asyncio.to_thread(Path(spec.overrides["computer.scoped.registry"]).mkdir)
    cfg = build_config(spec)
    # The process is the production credential seam; no deployed credential is read.
    for name in credential_names(cfg.model_dump()):
        monkeypatch.setenv(name, "generated-preflight-credential")
    monkeypatch.setenv(cfg.media.encryption_key_env,
                       base64.b64encode(bytes(range(32))).decode())
    received, payloads = [], []
    apps = boundary_apps(cfg, received, payloads)
    playback = None
    if manifest := os.environ.get("NORA_COMPOSITION_SERVICES"):
        from evals.services import load_services

        plan = load_services(Path(manifest)).corpora["autonomous_journey"][
            "generated-later-recall-restraint-v1"]
        playback = plan.external.playback()
        recorded = playback.applications()
        for binding, service in playback.bindings.items():
            apps.setdefault(binding, recorded[service])
    clients = []
    original_client = boto3.client

    def observe_client(*args, **kwargs):
        client = original_client(*args, **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(boto3, "client", observe_client)
    if exit_mode == "missing_token":
        monkeypatch.setenv(cfg.computer.token_env, "")
    before = asyncio.all_tasks()
    expected = (pytest.raises(StartupError, match="unset or empty")
                if exit_mode == "missing_token" else
                pytest.raises(StartupError, match=r"needs \[notes\] enabled")
                if exit_mode == "startup_error" else
                pytest.raises(RuntimeError, match="generated exit")
                if exit_mode == "case_error" else nullcontext())
    with expected:
        async with IsolatedServices(
            database_url=clean_pg_url, applications=apps, redis=True,
        ) as env:
            if playback is not None:
                playback.bind(env)
            urls, redis_process = set(env.urls.values()), env._process
            subject = await obtain(replace(spec, environment=env))
            deps = subject.deps
            sessions = await assert_live_composition(
                subject, cfg, apps, env, spec, playback, received, payloads,
            )
            env.check()
            urls = set(env.urls.values())
            redis_process = env._process
            if exit_mode == "case_error":
                raise RuntimeError("generated exit")
            await subject.aclose()
    assert "MODEL_DISPATCH" not in received
    if exit_mode != "missing_token":
        assert "rustfs:head_bucket" in received and clients
    assert all(len(c._endpoint.http_session._manager.pools) == 0 for c in clients)
    if exit_mode in {"success", "case_error"}:
        assert_closed(subject, deps, sessions)
    assert not env.active and not env._subjects
    assert os.environ[cfg.computer.scoped.redis_url_env] == "generated-preflight-credential"
    assert os.environ[cfg.bridges.redis_url_env] == "generated-preflight-credential"
    assert redis_process.returncode is not None
    assert not (asyncio.all_tasks() - before - {asyncio.current_task()})
    for url in urls:
        from urllib.parse import urlsplit
        address = urlsplit(url)
        with socket.socket() as probe:
            assert probe.connect_ex((address.hostname, address.port)) != 0
    composition = "obtained" if exit_mode in {"success", "case_error"} else "startup_refused"
    print(json.dumps({"exit_mode": exit_mode, "composition": composition,
                      "requests": received,
                      "model_dispatches": 0, "listeners_closed": len(urls),
                      "redis_stopped": True, "background_closed": True}))


async def assert_live_composition(subject, cfg, apps, env, spec, playback, received, payloads):
    deps = subject.deps
    assert subject.cfg.credentials[cfg.computer.scoped.redis_url_env] == env.redis_url
    expected_config = cfg.model_dump()
    actual_config = subject.cfg.model_dump()
    for path in (*apps, "store.database_url_env"):
        left, right = expected_config, actual_config
        parts = path.split(".")
        for part in parts[:-1]:
            left, right = left[part], right[part]
        left[parts[-1]] = right[parts[-1]]
    assert actual_config == expected_config

    assert set(deps.registry.adapters) == {"signal", "discord", "whatsapp", "instagram"}
    assert deps.computer_lifecycle is not None
    sessions = [getattr(adapter, "_delegate", adapter)._session
                for adapter in deps.registry.adapters.values()]
    assert all(session is not None and not session.closed for session in sessions)
    with pytest.raises(RuntimeError, match="second owner"):
        SegmentRegistry(pending_dir=subject.cfg.store.data_dir / "state" / "computer")
    assert isinstance(deps.blobs, RustfsBlobStore)
    assert deps.assembler and deps.dreamer_runtime and deps.background._tasks
    if playback is not None and deps.oauth_keepalive is not None:
        for lane, _ in deps.oauth_keepalive._lanes:
            result = await lane.refresh(unless_refreshed_since=0.0)
            assert not isinstance(result, str), result
            filename = f"{lane.lane.replace('_', '-')}-oauth.json"
            state = Path(subject.cfg.image_gen.state_dir) / filename
            assert state.is_relative_to(spec.workdir)
            assert json.loads(state.read_text())["refresh_token"].startswith("generated-")
        assert set(deps.oauth_keepalive.lanes) == {"xai", "openai_codex"}

    assert {f"{name}:{ACCOUNT_PATH}" for name in deps.registry.adapters} <= set(received)
    assert "rustfs:head_bucket" in received
    assert "MODEL_DISPATCH" not in received
    assert len(deps.blobs._s3._endpoint.http_session._manager.pools) == 1
    async with deps.audit._engine.connect() as connection:
        assert (await connection.execute(text("select 1"))).scalar() == 1
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(OTLPSpanExporter(
        endpoint=env.urls["obs.tracing.endpoint"] + "/v1/traces")))

    def emit():
        tracer = provider.get_tracer("generated-preflight")
        with tracer.start_as_current_span("composition"):
            pass
        provider.shutdown()

    await asyncio.to_thread(emit)
    assert [span.name for payload in payloads for resource in payload.resource_spans
            for scope in resource.scope_spans for span in scope.spans] == ["composition"]
    return sessions


def assert_closed(subject, deps, sessions):
    assert deps.client.is_closed and all(client.is_closed for client in deps.model_clients)
    assert not deps.background._tasks
    assert all(session.closed for session in sessions)
    journal = SegmentRegistry(pending_dir=subject.cfg.store.data_dir / "state" / "computer")
    journal.close()
    assert deps.audit._engine.pool.checkedout() == 0


def preflight_spec(source, evaluation_config, clean_pg_url, tmp_path, exit_mode):
    overrides = {"computer.scoped.registry": str(tmp_path / "scopes"),
                 "computer.scoped.lifecycle_socket": str(tmp_path / "lifecycle.sock")}
    if source == evaluation_config:
        overrides["cognition.enabled"] = True
        if exit_mode == "startup_error":
            # `[cognition] enabled` requires the notebook; no_home_room only
            # warns and leaves the unit un-woken, so it cannot refuse a boot.
            overrides["notes.enabled"] = False
        overrides.update({
            "computer.enabled": True, "computer.scoped.enabled": True,
            "media.enabled": True, "media.rustfs.enabled": True,
            "media.encryption_key_env": "MEDIA_ENCRYPTION_KEY",
            "obs.tracing.endpoint": "http://127.0.0.1:14318/v1/traces",
            "media.rustfs.endpoint": "http://rustfs.generated:9000",
            **{f"platforms.{name}.enabled": True for name in
               ("signal", "discord", "whatsapp", "instagram")},
            **{f"platforms.{name}.mode": "bridge" for name in
               ("signal", "discord", "whatsapp", "instagram")},
        })
    return SubjectSpec(database_url=clean_pg_url, workdir=tmp_path / "subject",
        config=source, overrides=overrides,
        policy=default_policy(("signal", "g1"), unit="generated-unit")
        + "home_room = true\n")
