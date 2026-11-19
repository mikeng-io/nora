"""Offline model transport, registration, and fail-closed browser retrieval."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import yaml
from openai import AsyncOpenAI

from nora.agent.browse import BrowseSettings
from nora.agent.registry import ToolRegistry
from nora.agent.room import Room
from nora.agent.web_browse import WebBrowseRunner
from nora.app.computer_queue import ComputerAdmission, SegmentRegistry
from nora.computer.wire import browse
from nora.domain.computer import BrowseOutcome
from nora.infra.computer.browser import ActionPolicy, Element, Snapshot
from nora.infra.computer.manager import TabUnavailable
from nora.obs.metrics import Metrics
from nora.persons import load_persons
from nora.testing import (
    FakeBlobStore,
    FakeBrowserRunner,
    FakeComputerEventStore,
    FakeComputerTaskStore,
    FakeSandboxRuntime,
    FakeStreamRedis,
)

ALIAS = "configured-flash"
SITE = "https://offers.example.org/item"
ROOM = "generated-room"
UNIT = "generated-unit"


def registry_for(web_browser, manager, admission, *, unit=UNIT, **kwargs):
    return ToolRegistry(
        unit_for=lambda *_: unit, computer=FakeSandboxRuntime(),
        blobs=FakeBlobStore(), computer_events=FakeComputerEventStore(),
        computer_admission=admission, browser_manager=manager, web_browser=web_browser,
        **kwargs,
    )


def bindings_for(tmp_path):
    path = tmp_path / "identity.toml"
    path.write_text('[persons.generated]\nfirst_name="Generated"\n')
    persons = load_persons(path)
    return {
        "store": None, "conversation_id": ROOM, "conversation_type": "group", "persons": persons,
        "honcho": None, "deliverer": None, "metrics": Metrics(),
        "room": Room(platform="signal", conversation_id=ROOM, persons=persons, roster_source=None),
    }


def admission_for_tests(tmp_path):
    return ComputerAdmission(
        tasks=FakeComputerTaskStore(), redis=FakeStreamRedis(),
        clock=lambda: datetime.now(UTC),
        executors=SegmentRegistry(pending_dir=tmp_path / "journal"),
        reconcile=AsyncMock(return_value="quiet"),
    )


def gateway_reply(content, *, used=5):
    return httpx.Response(200, json={
        "id": "generated", "object": "chat.completion", "model": "served-flash",
        "created": 1, "choices": [{
            "index": 0, "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 100, "completion_tokens": used, "total_tokens": 100 + used},
    })


class Page:
    def __init__(self):
        self.target_id = "generated-target"
        self.navigate = AsyncMock(return_value=SITE)
        self.snapshot = AsyncMock(return_value=Snapshot(
            SITE, "Offers", (Element("e1", "button", "Show offers"),),
        ))
        self.read_text = AsyncMock(return_value=("Published offer: HKD 399", False))
        self._call_on = AsyncMock(return_value=["button", "Show offers", "", []])
        self._object = AsyncMock(return_value="node")
        self._loader = AsyncMock(return_value="loader")
        self._page = AsyncMock(return_value={"result": {"value": True}})
        self.settle = AsyncMock()
        self.revoked = False
        self.policy = ActionPolicy()

    def revoke(self):
        self.policy.revoke()
        self.revoked = True


class Manager:
    def __init__(self, page):
        self.page = page
        self.opened = 0

    @asynccontextmanager
    async def acquire(self, scope, owner, *, must_exist=False):
        if must_exist:
            raise TabUnavailable("expired")
        self.opened += 1
        yield SimpleNamespace(page=self.page)


async def call_registered(tmp_path, browser, manager, *, admission=None, **kwargs):
    registry = registry_for(browser, manager, admission or admission_for_tests(tmp_path))
    spec = next(s for s in registry.specs(**bindings_for(tmp_path)) if s.name == "web_browse")
    args = {"objective": "Check the public offer.", "start_url": SITE, **kwargs}
    return await spec.call(**args)


@pytest.mark.parametrize("decision", ["1-999", '{"id":"1-1"}', "2-1"])
async def test_web_browse_rejects_invalid_action(tmp_path, decision):
    page = Page()
    manager = Manager(page)
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return gateway_reply(decision)

    async with AsyncOpenAI(base_url="http://gateway.invalid/v1", api_key="local",
                           http_client=httpx.AsyncClient(
                               transport=httpx.MockTransport(handler),
                           )) as c:
        runner = WebBrowseRunner(client=c, model=ALIAS, max_tokens=512,
                                 settings=BrowseSettings(), manager=manager)
        result = await call_registered(tmp_path, runner, manager)
    assert result["status"] == "unavailable"
    assert result["reason"] == "the model did not select a current action"
    assert "Show offers" in requests[0]["messages"][1]["content"]
    assert manager.opened == 1 and page.navigate.await_count == 1
    assert page._page.await_count == 0 and page.revoked


async def _scripted_run(tmp_path, replies, settings):
    page = Page()
    manager = Manager(page)
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return gateway_reply(replies[len(requests) - 1], used=1)

    async with AsyncOpenAI(base_url="http://gateway.invalid/v1", api_key="local",
                           http_client=httpx.AsyncClient(
                               transport=httpx.MockTransport(handler),
                           )) as c:
        runner = WebBrowseRunner(client=c, model=ALIAS, max_tokens=512,
                                 settings=settings, manager=manager)
        result = await call_registered(tmp_path, runner, manager)
    return result, requests, page


_NOT_SHOWN = json.dumps({"answer": "The offer table was not reached.", "found": False})


async def test_a_run_cut_at_its_limit_reports_what_it_read_not_nothing_found(tmp_path):
    result, requests, page = await _scripted_run(
        tmp_path, ["1-1", "2-finish", _NOT_SHOWN], BrowseSettings(max_steps=2),
    )
    assert result["status"] == "unavailable"
    assert result["answer"] == "The offer table was not reached."
    assert result["task"] and result["tab"] == "generated-target"
    assert "stopped early at its limit" in requests[2]["messages"][1]["content"]
    assert page._page.await_count == 1


async def test_a_covered_page_without_the_fact_is_still_nothing_found(tmp_path):
    result, requests, _ = await _scripted_run(
        tmp_path, ["1-finish", _NOT_SHOWN], BrowseSettings(),
    )
    assert result["status"] == "nothing_found"
    assert "stopped early" not in requests[1]["messages"][1]["content"]


async def test_a_reply_with_no_id_reports_from_the_evidence_without_acting(tmp_path):
    answer = json.dumps({"answer": "HKD 399, stock unconfirmed.", "found": True})
    result, requests, page = await _scripted_run(
        tmp_path, ["The published offer is HKD 399", answer], BrowseSettings(),
    )
    assert result["status"] == "ok" and result["answer"] == "HKD 399, stock unconfirmed."
    assert len(requests) == 2 and page._page.await_count == 0
    assert "The published offer is HKD 399" not in requests[1]["messages"][1]["content"]


async def test_a_later_decision_is_told_which_actions_were_already_taken(tmp_path):
    answer = json.dumps({"answer": "HKD 399.", "found": True})
    result, requests, _ = await _scripted_run(
        tmp_path, ["1-1", "2-finish", answer], BrowseSettings(),
    )
    prompts = [req["messages"][1]["content"] for req in requests]
    assert result["status"] == "ok"
    assert 'is="done"' not in prompts[0]
    assert 'is="done"' in prompts[1] and "Click Show offers" in prompts[1].split('is="done"')[1]
    assert 'is="done"' not in prompts[2]


async def test_web_browse_requires_owned_scope(tmp_path):
    page = Page()
    manager = Manager(page)
    registry = registry_for(FakeBrowserRunner(), manager, None)
    assert "web_browse" not in registry.surface(**bindings_for(tmp_path)).admitted

    def unexpected(_request):
        pytest.fail("an expired task reached the gateway")

    async with AsyncOpenAI(base_url="http://gateway.invalid/v1", api_key="local",
                           http_client=httpx.AsyncClient(
                               transport=httpx.MockTransport(unexpected),
                           )) as client:
        runner = WebBrowseRunner(client=client, model=ALIAS, max_tokens=512,
                                 settings=BrowseSettings(), manager=manager)
        result = await call_registered(tmp_path, runner, manager, task="0" * 32)
    assert result["status"] == "unavailable" and result["reason"] == "expired"
    assert manager.opened == 0 and page.navigate.await_count == 0


@pytest.mark.parametrize("mode", ["turns", "steps", "output", "total", "deadline", "cancel"])
async def test_web_browse_obeys_budget_and_cancellation(mode):
    page, entered = Page(), asyncio.Event()
    settings = {
        "turns": BrowseSettings(turns=2),
        "steps": BrowseSettings(max_steps=1),
        "output": BrowseSettings(output_tokens=3),
        "total": BrowseSettings(total_tokens=100),
        "deadline": BrowseSettings(timeout_seconds=0.02),
        "cancel": BrowseSettings(),
    }[mode]
    requests = []

    async def handler(request):
        requests.append(json.loads(request.content))
        entered.set()
        if mode in {"deadline", "cancel"}:
            await asyncio.Future()
        if len(requests) > 1:
            return gateway_reply('{"answer":"Price HKD 399.","found":true}', used=1)
        return gateway_reply("1-finish" if mode in {"steps", "output"} else "1-1", used=1)

    async with AsyncOpenAI(base_url="http://gateway.invalid/v1", api_key="local",
                           http_client=httpx.AsyncClient(
                               transport=httpx.MockTransport(handler),
                           )) as c:
        runner = WebBrowseRunner(client=c, model=ALIAS, max_tokens=512,
                                 settings=settings, manager=Manager(page))
        work = asyncio.create_task(runner.browse(scope="a", objective="Check the offer",
                                                 start_url=SITE))
        if mode == "cancel":
            async with asyncio.timeout(2):
                await entered.wait()
            work.cancel()
            with pytest.raises(asyncio.CancelledError):
                await work
        else:
            result = await work
            assert result.status == ("ok" if mode in {"steps", "output"} else "unavailable")
    assert len(requests) == {"total": 0, "steps": 2, "output": 2}.get(mode, 1)
    assert page._page.await_count == 0
    assert page.revoked
    if mode in {"steps", "turns"}:
        assert "Show offers" not in requests[0]["messages"][1]["content"]
    if mode == "output":
        assert [req["max_tokens"] for req in requests] == [2, 2]


async def test_web_browse_uses_configured_gateway(tmp_path):
    requests = []
    page = Page()
    manager = Manager(page)

    def handler(request):
        requests.append((str(request.url), json.loads(request.content)))
        if len(requests) == 1:
            return gateway_reply("1-finish")
        return gateway_reply('{"answer":"Price HKD 399. Stock unconfirmed.","found":true}')

    async with AsyncOpenAI(base_url="http://gateway.invalid/v1", api_key="local",
                           http_client=httpx.AsyncClient(
                               transport=httpx.MockTransport(handler),
                           )) as client:
        credential_env = "LOCAL_TEST_TOKEN"
        cfg = SimpleNamespace(
            credentials={credential_env: "synthetic"},
            computer=SimpleNamespace(
                browse=SimpleNamespace(
                    enabled=True, model=ALIAS, max_tokens=320, temperature=0,
                    **vars(BrowseSettings()),
                ),
                base_url="", token_env=credential_env,
            ),
        )
        wired = browse(manager, cfg, FakeSandboxRuntime(),
                       {"browse": client}.__getitem__, lambda purpose, model: model)
        result = await call_registered(tmp_path, wired.web_browser, manager)
    assert result["status"] == "ok" and result["url"] == SITE
    assert result["answer"] == "Price HKD 399. Stock unconfirmed."
    assert page.snapshot.await_count == page.read_text.await_count == 1
    assert [body["max_tokens"] for _, body in requests] == [64, 320]
    for url, body in requests:
        assert url == "http://gateway.invalid/v1/chat/completions" and body["model"] == ALIAS
        assert len(json.dumps(body["messages"]).encode()) < 40_000
        assert "tools" not in body


async def test_web_browse_registered_siblings_keep_contracts(tmp_path):
    browser = FakeBrowserRunner(outcome=BrowseOutcome("ok", answer="Observed", url=SITE))
    registry = registry_for(None, None, None, browser=browser)
    specs = {s.name: s for s in registry.specs(**bindings_for(tmp_path))}
    got = await specs["computer_browse"].call(objective="Read offers", start_url=SITE)
    fetched = await specs["web_fetch"].call(url=SITE)
    searched = await specs["web_search"].call(query="generated fixture offer")
    assert got["status"] == "ok" and got["answer"] == "Observed" and got["url"] == SITE
    assert fetched["status"] == searched["status"] == "unavailable"
    assert all(reply["note"] for reply in (got, fetched, searched))


def test_web_browse_routing_cases_pin_siblings():
    path = Path(__file__).resolve().parents[2] / "evals/corpora/tool_selection.yaml"
    cases = {case["name"]: case["metadata"] for case in yaml.safe_load(path.read_text())["cases"]
             if "metadata" in case}
    assert cases["route_web_browse_interactive_evidence"]["tools"] == ["web_browse"]
    assert {"web_fetch", "web_search", "computer_browse", "computer_run"} <= set(
        cases["route_web_browse_interactive_evidence"]["not_tools"],
    )
    for name in ("route_web_fetch_not_the_computer", "route_web_search_control"):
        assert "web_browse" in cases[name]["not_tools"]


async def test_web_browse_enforces_retrieval_boundary(tmp_path):
    page = Page()
    manager = Manager(page)
    requests = []
    admission = admission_for_tests(tmp_path)
    first_choice = "1-1"

    def script(request):
        payload = json.loads(request.content)
        requests.append(payload)
        prompt = payload["messages"][1]["content"]
        if len(requests) == 1:
            assert f'"{first_choice}": "Click Show offers"' in prompt
        else:
            assert '"2-1": "Click Show offers"' in prompt
            assert first_choice not in prompt
        return gateway_reply(first_choice)

    async with AsyncOpenAI(base_url="http://gateway.invalid/v1", api_key="local",
                           http_client=httpx.AsyncClient(
                               transport=httpx.MockTransport(script),
                           )) as client:
        runner = WebBrowseRunner(client=client, model=ALIAS, max_tokens=512,
                                 settings=BrowseSettings(), manager=manager)
        for url in ("file:///example.html", "https://generated:@localhost/"):
            refused = await call_registered(
                tmp_path, runner, manager, admission=admission, start_url=url,
            )
            assert refused["status"] == "unavailable"
        assert manager.opened == 0
        assert not requests
        result = await call_registered(tmp_path, runner, manager, admission=admission)
    assert "current action" in result["reason"]
    assert "1-1" in requests[0]["messages"][1]["content"]
    assert "2-1" in requests[1]["messages"][1]["content"]
    assert len(requests) == 2 and page._page.await_count == 1
