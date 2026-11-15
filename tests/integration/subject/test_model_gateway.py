"""The declared model gateway is the one authorized external egress.

A real local listener stands in for the configured model API. The
environment admits a dispatch only while it names the declared upstream,
route, method and model identity through the installed `RunBudget`; every
other destination, method, path, model or redirect is refused before any
bytes leave the process.
"""

import asyncio
import json
import socket
from contextlib import contextmanager
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
from aiohttp import web

from nora.agent import factory
from nora.domain.run_budget import RunBudget, RunBudgetError, active_run_budget
from nora.subject.environment import IsolatedServices
from nora.subject.eval_evidence import EvidenceError
from nora.subject.model_gateway import ModelGateway


def _body(model: str = "nora") -> dict[str, Any]:
    return {
        "model": model,
        "messages": [{"role": "user", "content": "generated"}],
        "max_tokens": 16,
        "temperature": 0,
        "tools": [{"type": "function",
                   "function": {"name": "generated_tool",
                                "parameters": {"type": "object",
                                               "properties": {}}}}],
    }


def _completion(model: str) -> dict[str, Any]:
    return {
        "id": "generated", "object": "chat.completion", "model": model,
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": "ok"}}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 2,
                  "total_tokens": 9},
    }


def _sse(body: dict[str, Any]) -> str:
    chunks = [
        {"id": "g", "object": "chat.completion.chunk", "model": body["model"],
         "choices": [{"index": 0,
                      "delta": {"role": "assistant", "content": "ok"}}]},
        {"id": "g", "object": "chat.completion.chunk", "model": body["model"],
         "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        {"id": "g", "object": "chat.completion.chunk", "model": body["model"],
         "choices": [],
         "usage": {"prompt_tokens": 11, "completion_tokens": 4,
                   "total_tokens": 15}},
    ]
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


@pytest.fixture
async def upstream():
    """The sole generated upstream — a real listener recording every arrival."""
    seen: dict[str, Any] = {"requests": [], "headers": [], "respond": None,
                            "release": asyncio.Event()}
    app = web.Application()

    async def respond(request: web.Request) -> web.StreamResponse:
        body = json.loads(await request.read())
        seen["requests"].append(body)
        seen["headers"].append(dict(request.headers))
        if seen["respond"] is not None:
            return await seen["respond"](request, body)
        return web.json_response(
            _completion(body["model"]),
            headers={"x-served-model": body["model"],
                     "x-served-provider": "generated"})

    app.router.add_post("/v1/chat/completions", respond)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0, shutdown_timeout=0.5).start()
    port = runner.addresses[0][1]
    yield f"http://127.0.0.1:{port}/v1", seen
    seen["release"].set()
    await runner.cleanup()


@pytest.fixture
def environment(upstream):
    """An `IsolatedServices` opened inside each test's own task.

    Entering and exiting inside the test task is the same discipline the
    recorded-service cases follow: the environment owns the tasks the case
    spawned, and nothing outside its scope is cancelled on close.
    """
    url, _ = upstream

    def open_env(**kwargs: Any) -> IsolatedServices:
        kwargs.setdefault(
            "model_gateway",
            ModelGateway(upstream=url, models=frozenset({"nora", "nora-judge"})))
        return IsolatedServices(
            database_url="sqlite://", applications={}, **kwargs)

    return open_env


@contextmanager
def _budget(**kwargs: Any):
    budget = RunBudget(max_output_tokens=10_000, output_reserve=256, **kwargs)
    token = active_run_budget.set(budget)
    try:
        yield budget
    finally:
        active_run_budget.reset(token)


async def test_admitted_dispatch_preserves_the_production_request_and_counts_once(
        upstream, environment):
    url, seen = upstream
    async with environment() as services:
        with _budget(max_requests=4) as budget:
            async with factory.create_client(
                    base_url=url, api_key="generated-key") as client:
                result = await client.chat.completions.with_raw_response.create(
                    **_body("nora"), extra_headers={"x-case-marker": "generated"})
            assert result.http_response.status_code == 200
            # served-model identity and provider headers pass back untouched
            assert result.http_response.headers["x-served-model"] == "nora"
            assert result.http_response.headers["x-served-provider"] == "generated"
            assert result.parse().model == "nora"
            # the request reached the upstream verbatim — alias, context, headers
            assert len(seen["requests"]) == 1
            assert seen["requests"][0]["model"] == "nora"
            assert seen["requests"][0]["messages"][0]["content"] == "generated"
            assert seen["requests"][0]["tools"][0]["function"]["name"] == "generated_tool"
            assert seen["headers"][0]["x-case-marker"] == "generated"
            # one admitted dispatch counted exactly once through RunBudget
            assert budget.requests == 1
            assert budget.input_tokens == 7 and budget.output_tokens == 2
            services.check()
        assert len(seen["requests"]) == 1


async def test_streamed_dispatch_reconciles_terminal_usage(upstream, environment):
    url, seen = upstream
    async with environment() as services:
        async def respond(request, body):
            return web.Response(text=_sse(body),
                                content_type="text/event-stream")

        seen["respond"] = respond
        with _budget(max_requests=4) as budget:
            async with factory.create_client(
                    base_url=url, api_key="generated-key") as client:
                stream = await client.chat.completions.create(
                    **_body("nora"), stream=True,
                    stream_options={"include_usage": True})
                chunks = [chunk async for chunk in stream]
            assert chunks and chunks[-1].choices == []
            assert budget.requests == 1
            assert budget.input_tokens == 11 and budget.output_tokens == 4
            services.check()
        assert len(seen["requests"]) == 1


@pytest.mark.parametrize(
    "cause,method,path,model",
    [
        ("external_dependency_not_isolated", "POST",
         "http://10.255.255.1:9/v1/chat/completions", "nora"),
        ("model_method_undeclared", "GET", ":route", "nora"),
        ("model_path_undeclared", "POST", ":v1/models", "nora"),
        ("model_identity_undeclared", "POST", ":route", "nora-other"),
    ],
)
async def test_an_undeclared_dispatch_is_refused_before_the_upstream(
        upstream, environment, cause, method, path, model):
    url, seen = upstream
    if path == ":route":
        target = url + "/chat/completions"
    elif path.startswith(":"):
        target = url + path[1:]
    else:
        target = path
    async with environment() as services:
        async with httpx.AsyncClient() as client:
            with pytest.raises(PermissionError, match="not isolated"):
                await client.request(method, target, json=_body(model))
        assert seen["requests"] == []
        with pytest.raises(EvidenceError, match=cause):
            services.check()


async def test_an_unbudgeted_dispatch_never_reaches_the_upstream(
        upstream, environment):
    url, seen = upstream
    async with environment() as services:
        async with httpx.AsyncClient() as plain:
            # no installed budget at all
            with pytest.raises(PermissionError, match="not isolated"):
                await plain.post(url + "/chat/completions", json=_body())
        with _budget(max_requests=4):
            async with httpx.AsyncClient() as unwrapped:
                # a budget is installed but this client's transport is not bound to it
                with pytest.raises(PermissionError, match="not isolated"):
                    await unwrapped.post(url + "/chat/completions", json=_body())
        assert seen["requests"] == []
        with pytest.raises(EvidenceError, match="model_dispatch_unbudgeted"):
            services.check()


async def test_a_mounted_unbudgeted_transport_is_refused_before_the_upstream(
        upstream, environment):
    url, seen = upstream
    from nora.agent.run_budget_transport import _BoundedTransport
    origin = urlsplit(url)
    mount = f"{origin.scheme}://{origin.netloc}"
    async with environment() as services:
        with _budget(max_requests=4) as budget:
            # the client's default transport IS bounded, but the URL mount
            # selects the transport that actually dispatches this request
            async with httpx.AsyncClient(
                    transport=_BoundedTransport(
                        httpx.AsyncHTTPTransport(), budget),
                    mounts={mount: httpx.AsyncHTTPTransport()}) as client:
                with pytest.raises(PermissionError, match="not isolated"):
                    await client.post(url + "/chat/completions", json=_body())
        assert seen["requests"] == []
        assert budget.requests == 0
        with pytest.raises(EvidenceError, match="model_dispatch_unbudgeted"):
            services.check()


async def test_a_mounted_budgeted_transport_is_admitted_and_counts_once(
        upstream, environment):
    url, seen = upstream
    from nora.agent.run_budget_transport import _BoundedTransport
    origin = urlsplit(url)
    mount = f"{origin.scheme}://{origin.netloc}"
    async with environment() as services:
        with _budget(max_requests=4) as budget:
            # the default transport is unbudgeted; the selected mount's
            # bounded transport is what must count this dispatch
            async with httpx.AsyncClient(
                    transport=httpx.AsyncHTTPTransport(),
                    mounts={mount: _BoundedTransport(
                        httpx.AsyncHTTPTransport(), budget)}) as client:
                response = await client.post(
                    url + "/chat/completions", json=_body())
            assert response.status_code == 200
            assert response.headers["x-served-model"] == "nora"
        assert len(seen["requests"]) == 1
        assert seen["requests"][0]["model"] == "nora"
        assert budget.requests == 1
        assert budget.input_tokens == 7 and budget.output_tokens == 2
        services.check()


async def test_an_exhausted_budget_refuses_before_the_upstream(upstream, environment):
    url, seen = upstream
    async with environment() as services:
        with _budget(max_requests=1) as budget:
            async with factory.create_client(
                    base_url=url, api_key="generated-key") as client:
                await client.chat.completions.create(**_body())
                assert len(seen["requests"]) == 1 and budget.requests == 1
                with pytest.raises(RunBudgetError):
                    await client.chat.completions.create(**_body())
        assert len(seen["requests"]) == 1
        services.check()


async def test_a_followed_redirect_is_refused_before_dispatch(upstream, environment):
    url, seen = upstream
    async with environment() as services:
        async def redirect(request, body):
            # the redirect response must still carry usage: the bounded
            # transport reconciles hop 1 before httpx can follow hop 2
            return web.json_response(
                {"usage": {"prompt_tokens": 7, "completion_tokens": 2,
                           "total_tokens": 9}},
                status=302,
                headers={"Location":
                         f"http://{request.host}/v1/chat/completions"})

        seen["respond"] = redirect
        with _budget(max_requests=4):
            from nora.agent.run_budget_transport import bounded_http_client
            client = bounded_http_client(httpx.AsyncClient(follow_redirects=True))
            client.follow_redirects = True
            try:
                with pytest.raises(PermissionError, match="not isolated"):
                    await client.post(url + "/chat/completions", json=_body())
            finally:
                await client.aclose()
        # the upstream saw the original POST; the redirected GET never dispatched
        assert len(seen["requests"]) == 1
        with pytest.raises(EvidenceError, match="model_method_undeclared"):
            services.check()


@pytest.mark.parametrize("status", [307, 308])
async def test_a_post_preserving_redirect_creates_no_second_attempt(
        upstream, environment, status):
    url, seen = upstream
    async with environment() as services:
        async def redirect(request, body):
            return web.json_response(
                {"usage": {"prompt_tokens": 7, "completion_tokens": 2,
                           "total_tokens": 9}},
                status=status,
                headers={"Location":
                         f"http://{request.host}/v1/chat/completions"})

        seen["respond"] = redirect
        with _budget(max_requests=4) as budget:
            from nora.agent.run_budget_transport import bounded_http_client
            client = bounded_http_client(httpx.AsyncClient(follow_redirects=True))
            client.follow_redirects = True
            try:
                with pytest.raises(PermissionError, match="not isolated"):
                    await client.post(url + "/chat/completions", json=_body())
            finally:
                await client.aclose()
        # the admitted POST reached upstream once; the redirect-built second
        # attempt — same method, route, model, selected transport — was refused
        assert len(seen["requests"]) == 1
        assert budget.requests == 1
        with pytest.raises(EvidenceError, match="model_dispatch_replayed"):
            services.check()


async def test_a_cancelled_dispatch_latches_the_budget(upstream, environment):
    url, seen = upstream
    async with environment() as services:
        async def hang(request, body):
            await seen["release"].wait()
            return web.json_response(_completion(body["model"]))

        seen["respond"] = hang
        with _budget(max_requests=4) as budget:
            async with factory.create_client(
                    base_url=url, api_key="generated-key") as client:
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(
                        client.chat.completions.create(**_body()), 1.0)
            # the admitted dispatch reached upstream; its indeterminate
            # completion is terminal for the budget, never silent
            assert len(seen["requests"]) == 1
            assert budget.spent is not None
        services.check()


async def test_the_declared_upstream_is_unreachable_between_dispatches(
        upstream, environment):
    url, seen = upstream
    port = urlsplit(url).port
    async with environment() as services:
        with _budget(max_requests=4):
            async with factory.create_client(
                    base_url=url, api_key="generated-key") as client:
                await client.chat.completions.create(**_body())
            assert len(seen["requests"]) == 1
            # no lingering exemption: the same upstream is refused outside an
            # admitted dispatch, at the socket layer and at the HTTP layer
            with pytest.raises(PermissionError):
                socket.create_connection(("127.0.0.1", port), timeout=1)
            async with httpx.AsyncClient() as plain:
                with pytest.raises(PermissionError):
                    await plain.post(url + "/chat/completions", json=_body())
        assert len(seen["requests"]) == 1
        with pytest.raises(EvidenceError):
            services.check()


async def test_other_external_effects_stay_refused(upstream, environment):
    _, seen = upstream
    async with environment() as services:
        async with httpx.AsyncClient() as client:
            with pytest.raises(PermissionError, match="not isolated"):
                await client.post("http://undeclared.invalid:9/x", json={})
        with pytest.raises(PermissionError):
            await asyncio.create_subprocess_exec("true")
        assert seen["requests"] == []
        with pytest.raises(EvidenceError, match="external_dependency_not_isolated"):
            services.check()


async def test_a_refusal_never_retains_credentials(upstream, environment):
    url, _ = upstream
    async with environment() as services:
        async with httpx.AsyncClient() as client:
            with pytest.raises(PermissionError):
                await client.get(
                    url + "/chat/completions",
                    headers={"Authorization": "Bearer generated-key"})
        denied = json.dumps(services.denied)
        assert "generated-key" not in denied
        assert all(set(row) == {"operation", "cause"} for row in services.denied)


async def test_bind_config_admits_the_declared_endpoint_only(
        upstream, environment, tmp_path, monkeypatch):
    url, _ = upstream
    async with environment() as services:
        monkeypatch.setenv("OMNIROUTE_API_KEY", "generated")
        from nora.subject.obtain import SubjectSpec, build_config

        cfg = build_config(SubjectSpec(
            database_url="sqlite://", workdir=tmp_path,
            overrides={"llm.base_url": url}))
        bound = services.bind_config(cfg)
        assert bound.llm.base_url == url
        assert not services.owns(url)  # it is the declared egress, not a listener

        other = build_config(SubjectSpec(
            database_url="sqlite://", workdir=tmp_path / "other",
            overrides={"llm.base_url": "http://undeclared.invalid:9/v1"}))
        with pytest.raises(ValueError, match="model dispatch"):
            services.bind_config(other)


def test_a_missing_or_malformed_declaration_is_refused():
    url = "http://127.0.0.1:1/v1"
    with pytest.raises(ValueError, match="model identities"):
        ModelGateway(upstream=url, models=frozenset())
    with pytest.raises(ValueError, match="plain http"):
        ModelGateway(upstream="ftp://upstream.invalid/v1",
                     models=frozenset({"nora"}))
    with pytest.raises(ValueError, match="plain http"):
        ModelGateway(upstream="https://user:pw@upstream.invalid/v1",
                     models=frozenset({"nora"}))
