"""A parsed `journey_world` manifest binds its declared model gateway.

`load_services` builds the `JourneyServices` the live-clock drive opens;
with `model_gateway` declared the world installs NO generated subject or
judge responders — the declared local upstream answers through the
environment's admitted egress, each dispatch counted once through the
installed `RunBudget`. An undeclared model, an unbudgeted client or an
endpoint outside the declared boundary refuses rather than rewriting.
"""

import json
from typing import Any

import httpx
import pytest
from aiohttp import web

from evals.services import load_services
from nora.agent import factory
from nora.subject.eval_evidence import EvidenceError
from nora.subject.obtain import SubjectSpec, build_config
from tests.integration.subject.test_model_gateway import _body, _budget, _completion


@pytest.fixture
async def upstream():
    """The sole generated upstream — a real listener recording every arrival."""
    seen: dict[str, Any] = {"requests": [], "headers": []}
    app = web.Application()

    async def respond(request: web.Request) -> web.StreamResponse:
        body = json.loads(await request.read())
        seen["requests"].append(body)
        seen["headers"].append(dict(request.headers))
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
    await runner.cleanup()


def _load_plan(tmp_path, upstream_url, *, models=("nora", "alpha", "beta")):
    """The parsed `journey_world` plan the live-clock selection binds."""
    manifest = {"version": 1, "corpora": {"autonomous_journey": {"the_outing": {
        "kind": "journey_world",
        "policy": "[rooms]\nallow = []",
        "judges": ["alpha", "beta"],
        "overrides": {"llm.base_url": upstream_url},
        "model_gateway": {"upstream": upstream_url,
                          "models": list(models)}}}}}
    path = tmp_path / "services.json"
    path.write_text(json.dumps(manifest))
    return load_services(path).corpora["autonomous_journey"]["the_outing"]


async def test_the_declared_world_admits_subject_and_judges_through_the_budget(
        upstream, tmp_path):
    url, seen = upstream
    plan = _load_plan(tmp_path, url)
    async with plan.open("sqlite://") as services:
        # The declared boundary replaces the world's generated responders:
        # no finite protocol, no generated judge, no owned model listener.
        assert plan.protocol is None and plan.judge is None
        assert "llm.base_url" not in services.urls
        with _budget(max_requests=8) as budget:
            async with factory.create_client(
                    base_url=url, api_key="generated-key") as client:
                responses = [
                    await client.chat.completions.with_raw_response.create(
                        **_body(model))
                    for model in ("nora", "alpha", "beta")
                ]
            assert all(r.http_response.status_code == 200 for r in responses)
            # one admitted subject request and the two distinct judge
            # requests, each counted exactly once
            assert budget.requests == 3
        # the upstream saw each final request — model alias and payload intact
        assert [r["model"] for r in seen["requests"]] == ["nora", "alpha", "beta"]
        assert all(r["messages"][0]["content"] == "generated"
                   for r in seen["requests"])
        services.check()


async def test_an_undeclared_model_is_refused_before_the_upstream(
        upstream, tmp_path):
    url, seen = upstream
    plan = _load_plan(tmp_path, url)
    async with plan.open("sqlite://") as services:
        with _budget(max_requests=8) as budget:
            async with factory.create_client(
                    base_url=url, api_key="generated-key") as client:
                await client.chat.completions.create(**_body("nora"))
                assert budget.requests == 1
                with pytest.raises(PermissionError, match="not isolated"):
                    await client.chat.completions.create(**_body("nora-undeclared"))
        assert [r["model"] for r in seen["requests"]] == ["nora"]
        with pytest.raises(EvidenceError, match="model_identity_undeclared"):
            services.check()


async def test_an_unbudgeted_dispatch_is_refused_before_the_upstream(
        upstream, tmp_path):
    url, seen = upstream
    plan = _load_plan(tmp_path, url)
    async with plan.open("sqlite://") as services:
        with _budget(max_requests=8) as budget:
            async with factory.create_client(
                    base_url=url, api_key="generated-key") as client:
                await client.chat.completions.create(**_body("nora"))
            assert budget.requests == 1
            # a budget is installed, but this client's transport is not bound to it
            async with httpx.AsyncClient() as unbudgeted:
                with pytest.raises(PermissionError, match="not isolated"):
                    await unbudgeted.post(
                        url + "/chat/completions", json=_body("nora"))
        assert len(seen["requests"]) == 1
        with pytest.raises(EvidenceError, match="model_dispatch_unbudgeted"):
            services.check()


async def test_an_endpoint_outside_the_declared_boundary_is_refused(
        upstream, tmp_path):
    url, _ = upstream
    plan = _load_plan(tmp_path, url)
    async with plan.open("sqlite://") as services:
        # The declared upstream IS the admitted endpoint — never rewritten,
        # only bound or refused.
        spec = SubjectSpec(database_url="sqlite://", workdir=tmp_path / "subject",
                           overrides={"llm.base_url": url})
        bound = services.bind_config(build_config(spec))
        assert bound.llm.base_url == url
        other = build_config(SubjectSpec(
            database_url="sqlite://", workdir=tmp_path / "other",
            overrides={"llm.base_url": "http://undeclared.invalid:9/v1"}))
        with pytest.raises(ValueError, match="model dispatch"):
            services.bind_config(other)
        services.check()
