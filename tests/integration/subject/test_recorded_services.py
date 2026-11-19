import asyncio
import json
import subprocess
import sys
from textwrap import dedent

import aiohttp
import pytest
from aiohttp import web

from nora.subject.environment import IsolatedServices
from nora.subject.eval_evidence import EvidenceError
from nora.subject.obtain import SubjectSpec, build_config
from nora.subject.recorded_services import RecordedServices


def fixture(tmp_path, rows):
    path = tmp_path / "recording.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "bindings": {"honcho.base_url": "one", "llm.base_url": "one"},
                "services": {"one": rows},
            }
        )
    )
    return RecordedServices.load(path)


def row(method="POST", path="/v1?q=1", **response):
    return {
        "method": method,
        "path": path,
        "request_json": {"id": "generated"},
        "response": response,
    }


@pytest.mark.asyncio
async def test_bind_generated_config_endpoints_and_reject_non_endpoints(
    tmp_path, evaluation_config, monkeypatch,
):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated")
    cfg = build_config(SubjectSpec(
        database_url="sqlite://", workdir=tmp_path,
        overrides={"memory.index.embedder_url": "https://embedder.example/v1/embed?q=1",
                   "documents.public_url": "https://example.invalid"},
    ))
    bindings = {"memory.index.embedder_url": "shared", "llm.base_url": "shared"}
    bindings.update({f"providers.{name}.base_url": "shared" for name in cfg.providers})
    data = {"version": 1, "bindings": bindings, "services": {"shared": [
        {"method": "GET", "path": "/v1/embed?q=1", "response": {"status": 200}}
    ]}}
    async with RecordedServices(data).open("sqlite://") as env:
        bound = env.bind_config(cfg)
        assert bound.memory.index.embedder_url == env.urls["llm.base_url"] + "/v1/embed?q=1"
        assert bound.documents.public_url == cfg.documents.public_url
        async with (aiohttp.ClientSession() as client,
                    client.get(bound.memory.index.embedder_url) as response):
            assert response.status == 200

    for path in ("documents.public_url", "llm.model", "memory.index.missing_url"):
        async with RecordedServices({"version": 1, "bindings": {path: "shared"},
                                     "services": {"shared": []}}).open("sqlite://") as env:
            with pytest.raises(ValueError, match="must name an HTTP endpoint"):
                env.bind_config(cfg)
    async with RecordedServices({"version": 1, "bindings": {
        "memory.index.embedder_url": "shared"}, "services": {"shared": []}}).open(
            "sqlite://") as env:
        with pytest.raises(ValueError, match="model dispatch"):
            env.bind_config(cfg)


@pytest.mark.asyncio
async def test_json_sse_shared_and_planned_404(tmp_path):
    recorded = fixture(
        tmp_path,
        [
            row(status=200, json={"id": "generated"}, headers={"Content-Type": "application/json"}),
            row(status=404),
            row(status=200, chunks=[{"delta": "Generated"}], headers={"x-model": "generated"}),
        ],
    )
    async with recorded.open("sqlite://") as env:
        assert env.urls["honcho.base_url"] == env.urls["llm.base_url"]
        async with aiohttp.ClientSession() as client:
            url = env.urls["llm.base_url"] + "/v1?q=1"
            async with client.post(url, json={"id": "generated"}) as result:
                assert result.status == 200
                assert result.content_type == "application/json"
                assert await result.json() == {"id": "generated"}
            async with client.post(url, json={"id": "generated"}) as result:
                assert result.status == 404 and await result.read() == b""
            async with client.post(url, json={"id": "generated"}) as result:
                assert result.headers["x-model"] == "generated"
                assert await result.text() == 'data: {"delta":"Generated"}\n\ndata: [DONE]\n\n'
        env.check()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path,body",
    [
        ("POST", "/wrong", {"id": "generated"}),
        ("GET", "/v1?q=1", {"id": "generated"}),
        ("POST", "/v1?q=1", {"id": "wrong"}),
    ],
)
async def test_unmatched_does_not_consume(tmp_path, method, path, body):
    from opentelemetry import trace

    from evals.backbone import _prefill_capture
    from nora.subject.eval_evidence import RunEvidence, read_events
    from nora.subject.sandbox import eval_host_lock

    _prefill_capture()
    recorded = fixture(tmp_path, [row(status=200, json={"ok": True})])
    with eval_host_lock() as lock_fd:
        evidence = RunEvidence(run_id="generated-mismatch", metadata={}, lock_fd=lock_fd,
                               private_root=tmp_path / "capture")
        with (evidence.capture(trace.get_tracer_provider()),
              pytest.raises(EvidenceError, match="external_response_not_recorded")):
            async with recorded.open("sqlite://") as env, aiohttp.ClientSession() as client:
                async with client.request(
                    method, env.urls["llm.base_url"] + path, json=body,
                ) as result:
                    assert result.status == 500
                with pytest.raises(EvidenceError, match="external_response_not_recorded"):
                    env.check()
                async with client.post(
                    env.urls["llm.base_url"] + "/v1?q=1", json={"id": "generated"}
                ) as result:
                    assert result.status == 200
                async with client.post(
                    env.urls["llm.base_url"] + "/v1?q=1", json={"id": "generated"}
                ) as result:
                    assert result.status == 500

    events = [e for e in read_events(evidence.path) if e["kind"] == "external_service_mismatch"]
    assert len(events) == 2
    first, exhausted = events
    actual = tuple(first.get(k) for k in ("service", "position", "method", "path"))
    assert actual == ("one", "0", method, path)
    assert (first["expected_method"], first["expected_path"]) == ("POST", "/v1?q=1")
    assert (exhausted["position"], exhausted["expected_path"]) == ("1", "exhausted")
    assert all(e["cause"] == "external_response_not_recorded" for e in events)


@pytest.mark.asyncio
async def test_missing_response_and_exception_cleanup(tmp_path):
    recorded = fixture(tmp_path, [row(status=200)])
    with pytest.raises(EvidenceError, match="external_response_not_consumed"):
        async with recorded.open("sqlite://") as env:
            assert env.guarded
    assert not env.active
    for exception in (RuntimeError("original"), asyncio.CancelledError()):
        with pytest.raises(type(exception)):
            async with recorded.open("sqlite://") as env:
                raise exception
        assert not env.active


def test_cleanup_can_be_requested_by_a_different_task():
    probe = dedent("""
        import asyncio

        from nora.subject.environment import IsolatedServices

        async def exercise():
            environment = IsolatedServices(database_url="sqlite://", applications={})
            await asyncio.create_task(environment.__aenter__())
            assert environment.guarded

            started = asyncio.Event()

            async def leak():
                started.set()
                await asyncio.Event().wait()

            leaked = asyncio.create_task(leak())
            await started.wait()
            await asyncio.create_task(environment.__aexit__(None, None, None))
            assert leaked.cancelled()
            assert not environment.active

        asyncio.run(exercise())
    """)
    # Keep the deadlock probe in a subprocess so a regression cannot hang pytest.
    try:
        result = subprocess.run(  # noqa: S603 -- fixed interpreter and probe
            [sys.executable, "-c", probe], capture_output=True, text=True, timeout=15,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("isolated services deadlocked during teardown in a different task")
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_concurrent_requests_consume_distinct_recorded_responses(monkeypatch, tmp_path):
    first_entered, second_entered, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    entered = []
    original = IsolatedServices._coverage

    def observed(self, service):
        coverage = original(self, service)

        @web.middleware
        async def arrival(request, handler):
            entered.append(request.method)
            (first_entered if len(entered) == 1 else second_entered).set()
            return await coverage(request, handler)

        return arrival

    monkeypatch.setattr(IsolatedServices, "_coverage", observed)

    async def slow_body():
        yield b'{"id":'
        await release.wait()
        yield b'"generated"}'

    recording = fixture(tmp_path, [row(status=200, json={"seq": 1}),
                                   row(status=200, json={"seq": 2})])
    async with recording.open("sqlite://") as env, aiohttp.ClientSession() as client:
        async with asyncio.timeout(5):
            url = env.urls["llm.base_url"] + "/v1?q=1"
            first = asyncio.create_task(client.post(url, data=slow_body()))
            await first_entered.wait()
            second = asyncio.create_task(client.post(url, json={"id": "generated"}))
            await second_entered.wait()
            release.set()
            async with await first as response:
                assert await response.json() == {"seq": 1}
            async with await second as response:
                assert await response.json() == {"seq": 2}


@pytest.mark.parametrize(
    "change",
    [
        lambda x: x.update(version=True),
        lambda x: x.update(extra=1),
        lambda x: x["bindings"].update({"llm.base_url": "missing"}),
        lambda x: x["services"]["one"][0]["response"].update(status=True),
        lambda x: x["services"]["one"][0]["response"].update(status=101),
        lambda x: x["services"]["one"][0]["response"].update(headers={"Content-Length": "1"}),
        lambda x: x["services"]["one"][0]["response"].update(json={}, chunks=[]),
        lambda x: x["services"]["one"][0].update(path="//bad"),
    ],
)
def test_reject_invalid_schema(tmp_path, change):
    data = {
        "version": 1,
        "bindings": {"llm.base_url": "one"},
        "services": {"one": [row(status=200)]},
    }
    change(data)
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        RecordedServices.load(path)


@pytest.mark.asyncio
@pytest.mark.parametrize(("paths", "expected_error"), [
    ((), None),
    (("/search", "/search"), None),
    (("/search",), "external_response_not_consumed"),
    (("/other",), "external_response_not_recorded"),
    (("/search", "/search", "/search"), "external_response_not_recorded"),
    (None, "external_response_not_consumed"),
])
async def test_routing_optional_provider_can_be_skipped_without_hiding_a_mismatch(
    tmp_path, paths, expected_error,
):
    from contextlib import nullcontext

    from evals.services import load_services

    plan = {
        "kind": "routing_recording",
        "model_gateway": {"upstream": "http://127.0.0.1:9/v1", "models": ["generated"]},
        "external": {
            "version": 1,
            "bindings": {"search.exa.url": "provider"},
            "services": {"provider": [row(path="/search", status=200)] * 2},
        },
    }
    if paths is not None:
        plan["optional_services"] = ["provider"]
    manifest = tmp_path / "services.json"
    manifest.write_text(json.dumps({"version": 1, "corpora": {
        "tool_selection": {"generated": plan},
    }}))
    recorded = load_services(manifest).corpora["tool_selection"]["generated"]
    expectation = (
        pytest.raises(EvidenceError, match=expected_error) if expected_error else nullcontext()
    )
    with expectation:
        async with recorded.open("sqlite://") as env:
            async with aiohttp.ClientSession() as client:
                endpoint = env.urls["search.exa.url"]
                for i, path in enumerate(paths or ()):
                    async with client.post(endpoint + path, json={"id": "generated"}) as response:
                        assert response.status == (200 if path == "/search" and i < 2 else 500)


@pytest.mark.asyncio
@pytest.mark.parametrize(("guilds", "ordinary", "error"), [
    ((), True, None),
    (("two", "one", "two", "two"), True, None),
    (("unknown",), True, "external_response_not_recorded"),
    (("wrong-op",), True, "external_response_not_recorded"),
    (("one",), False, "external_response_not_consumed"),
])
async def test_routing_discord_channel_reads_do_not_advance_recording(guilds, ordinary, error):
    from contextlib import nullcontext

    from nora.subject.model_gateway import ModelGateway

    binding = "platforms.discord.base_url"
    plan = RecordedServices(
        {"version": 1, "bindings": {binding: "bridge"}, "services": {
            "bridge": [row(path="/ordinary", status=200)],
        }},
        model_gateway=ModelGateway(
            upstream="http://127.0.0.1:9/v1", models=frozenset({"generated"}),
        ),
        background_discord_reads=[
            {"request_json": {"op": "channels", "params": {"guild_id": guild}},
             "response": {"status": 200, "json": {"available": False, "fixture": guild}}}
            for guild in ("one", "two")
        ],
    )
    with pytest.raises(EvidenceError, match=error) if error else nullcontext():
        async with plan.open("sqlite://") as env, aiohttp.ClientSession() as client:
            endpoint = env.urls[binding]
            for guild in guilds:
                op = "roster" if guild == "wrong-op" else "channels"
                params = {"guild_id": "one" if guild == "wrong-op" else guild}
                async with client.post(endpoint + "/v1/reads", json={
                    "op": op, "params": params,
                }) as response:
                    if guild in {"one", "two"}:
                        assert response.status == 200
                        assert (await response.json())["fixture"] == guild
                    else:
                        assert response.status == 500
            if ordinary:
                async with client.post(endpoint + "/ordinary", json={"id": "generated"}) as result:
                    assert result.status == 200
