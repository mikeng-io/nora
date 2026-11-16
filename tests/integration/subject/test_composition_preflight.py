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
from nora.subject.environment import IsolatedServices
from nora.subject.journey_world import memory_service
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
    apps["honcho.base_url"] = memory_service([])
    for name in ("signal", "discord", "whatsapp", "instagram"):
        app = web.Application()

        async def read(request, platform=name):
            received.append(f"{platform}:{request.path}")
            return web.json_response({
                "/v1/self": {"primary": f"generated-{platform}", "aliases": []},
                "/v1/health": {"ok": True},
                "/v1/groups": [],
            }[request.path])

        async def reads(request):
            body = await request.json()
            assert body["op"] in {"channels", "threads"}, body
            received.append(f"discord:{body['op']}")
            return web.json_response({"available": True, "value": []})

        for path in ("/v1/self", "/v1/health", "/v1/groups"):
            app.router.add_get(path, read)
        app.router.add_post("/v1/reads", reads)
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
                pytest.raises(StartupError, match="no unit can run")
                if exit_mode == "startup_error" else
                pytest.raises(RuntimeError, match="generated exit")
                if exit_mode == "case_error" else nullcontext())
    with expected:
        async with IsolatedServices(
            database_url=clean_pg_url, applications=apps, redis=True,
        ) as env:
            monkeypatch.setenv(cfg.computer.scoped.redis_url_env, env.redis_url)
            monkeypatch.setenv(cfg.bridges.redis_url_env, env.redis_url)
            urls, redis_process = set(env.urls.values()), env._process
            subject = await obtain(replace(spec, environment=env))
            deps = subject.deps
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
            assert {f"{name}:/v1/self" for name in deps.registry.adapters} <= set(received)
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
        + ("home_room = true\n" if exit_mode != "startup_error" else ""))
