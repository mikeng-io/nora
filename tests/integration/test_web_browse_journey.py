"""A real Chromium page, synthetic computerd relay and scripted OmniRoute replies."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import aiohttp
import httpx
import pytest
from aiohttp import web
from openai import AsyncOpenAI

from nora.agent.browse import BrowseSettings
from nora.agent.web_browse import WebBrowseRunner
from nora.app.computer_dispatch import ComputerToolDispatch
from nora.app.computer_queue import current_segment
from nora.domain.computer import computer_unit_scope
from nora.infra.computer.browser import CdpConnection, _attach_owned
from nora.infra.computer.manager import ScopeBrowserManager
from nora.policy import Conversation, Policy
from nora.testing import FakeBrowserTabStore
from tests.unit.test_web_browse_tool import (
    ALIAS,
    UNIT,
    admission_for_tests,
    bindings_for,
    gateway_reply,
    registry_for,
)

SITE = """<!doctype html><title>Offer lookup</title>
<main>
  <h1>EX4 offer lookup</h1>
  <button id="offers"
    onclick="document.querySelector('#district').hidden=false">Show offers</button>
  <label id="district" hidden>Branch
    <select aria-label="Branch" onchange="document.querySelector('#check').hidden=false">
      <option>Every branch</option><option>Wan Chai</option>
    </select>
  </label>
  <button hidden id="check" onclick="document.querySelector('#result').textContent =
    'EX4 at Wan Chai: HKD 399. Stock not published.'">Check stock</button>
  <p id="result"></p>
  <form method="post" action="/commit">
    <input type="password" aria-label="Password">
    <button>Commit purchase</button>
  </form>
</main>"""


def _chrome() -> str | None:
    if override := os.environ.get("NORA_TEST_CHROME"):
        if not Path(override).is_file():
            pytest.fail(f"Configured Chromium binary does not exist: {override}")
        return override
    candidates = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    roots = (Path.home() / ".cache/ms-playwright", Path.home() / "Library/Caches/ms-playwright")
    for root in roots:
        candidates.extend(str(path) for path in sorted(root.glob("chromium-*/*/chrome")))
    return next((path for path in candidates if path and Path(path).is_file()), None)


@contextlib.asynccontextmanager
async def _chromium(tmp_path):
    binary = _chrome()
    if binary is None:
        if os.environ.get("NORA_TEST_CHROME_REQUIRED"):
            pytest.fail("Chromium is required for the browser journey")
        pytest.skip("Chromium is unavailable")
    profile = tmp_path / "chrome"
    stderr_path = tmp_path / "chromium.stderr"
    with stderr_path.open("wb") as stderr:
        process = await asyncio.create_subprocess_exec(
            binary, "--headless", "--no-sandbox", "--disable-background-networking",
            "--no-proxy-server", "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
            "--remote-debugging-port=0", f"--user-data-dir={profile}", "about:blank",
            stdout=asyncio.subprocess.DEVNULL, stderr=stderr,
        )
    try:
        marker = profile / "DevToolsActivePort"
        try:
            async with asyncio.timeout(12):
                # Chromium signals readiness through this file in its isolated profile.
                while not marker.exists() or len(marker.read_text().splitlines()) < 2:
                    if process.returncode is not None:
                        pytest.fail(
                            f"Chromium {binary} exited {process.returncode} before listening. "
                            f"{stderr_path.read_text(errors='replace')[-4000:]}"
                        )
                    await asyncio.sleep(0.03)
        except TimeoutError:
            pytest.fail(
                f"Chromium {binary} did not publish its DevTools address. "
                f"{stderr_path.read_text(errors='replace')[-4000:]}"
            )
        port, socket = marker.read_text().splitlines()[:2]
        yield f"http://127.0.0.1:{port}{socket}"
    finally:
        if process.returncode is None:
            process.terminate()
        await process.wait()


def _relay(http, endpoint, scope, token, connections):
    async def serve(request):
        connections.append(request.query["scope"])
        assert request.query["scope"] == scope
        assert request.headers["X-Computerd-Instance"] == "generated-instance"
        assert request.headers["Authorization"] == f"Bearer {token}"
        downstream = web.WebSocketResponse()
        await downstream.prepare(request)
        async with http.ws_connect(endpoint) as upstream:
            async def copy(source, destination):
                async for message in source:
                    if message.type is aiohttp.WSMsgType.TEXT:
                        await destination.send_str(message.data)

            task = asyncio.create_task(copy(upstream, downstream))
            try:
                await copy(downstream, upstream)
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        return downstream

    return serve


async def _redraw(http, endpoint, tabs, scope):
    [tab] = await tabs.open_for_scope(scope, instance="generated-instance")
    async with http.ws_connect(endpoint) as ws:
        cdp = CdpConnection(ws)
        try:
            page = await _attach_owned(cdp, tab.target_id)
            await page.evaluate("""
                (() => {
                    const before = document.querySelector('#offers');
                    before.replaceWith(before.cloneNode(true));
                })()
            """)
        finally:
            await cdp.close()


async def _dispatch(registry, admission, bindings, unit, args):
    scope = computer_unit_scope(unit)
    room_id = bindings["conversation_id"]
    row = (await admission.submit(
        scope=scope, kind="browse", payload={"tool": "web_browse", "args": args},
        origin={"actor": "tag", "platform": "signal", "conversation_id": room_id,
                "conversation_type": "group", "requester_person_id": "generated"},
    )).task
    conversation = Conversation(
        platform="signal", ids=frozenset({room_id}), persons=frozenset({"generated"}),
        label="Generated", unit=unit,
    )
    policy = Policy(conversations=(conversation,), _by_id={("signal", room_id): conversation})
    dispatch = ComputerToolDispatch(
        registry=registry, store=None, persons=bindings["persons"], honcho=None,
        metrics=bindings["metrics"], policy=policy, scope_for=lambda *_: scope,
    )
    task = asyncio.current_task()
    admission.executors.register(row.id, task)
    token = current_segment.set(row.id)
    try:
        return await dispatch(row)
    finally:
        current_segment.reset(token)
        admission.executors.unregister(row.id, task)


def _decision(prompt, label):
    matched = re.search(r'"([0-9]+-[0-9]+)": "' + re.escape(label) + r'"', prompt)
    assert matched is not None, prompt
    return matched[1]


async def test_registered_web_browse_journey(tmp_path, aiohttp_server):
    scope = computer_unit_scope(UNIT)
    commits = []
    connections = []
    visits = []

    async def offer(request):
        visits.append(request.path_qs)
        return web.Response(text=SITE, content_type="text/html")

    async def commit(request):
        commits.append(request.method)
        return web.Response(text="unexpected submit")

    async with _chromium(tmp_path) as endpoint, aiohttp.ClientSession() as http:
        app = web.Application()
        app.router.add_get("/offer", offer)
        app.router.add_post("/commit", commit)
        token = uuid4().hex
        app.router.add_get("/v1/browser", _relay(http, endpoint, scope, token, connections))
        server = await aiohttp_server(app)
        url = str(server.make_url("/offer"))
        base = str(server.make_url("/"))
        tabs = FakeBrowserTabStore()
        runtime = SimpleNamespace(binding=AsyncMock(return_value=SimpleNamespace(
            endpoint=SimpleNamespace(base_url=base), instance="generated-instance",
        )))
        manager = ScopeBrowserManager(
            runtime=runtime, tabs=tabs, token=token,
            clock=lambda: datetime.now(UTC), idle_after=timedelta(minutes=5),
        )
        requests = []

        async def script(request):
            body = json.loads(request.content)
            prompt = body["messages"][1]["content"]
            requests.append(prompt)
            assert re.search(r'": "Click Commit purchase"', prompt) is None
            if len(requests) == 1:
                await _redraw(http, endpoint, tabs, scope)
                return gateway_reply(_decision(prompt, "Click Show offers"))
            if len(requests) == 2:
                assert "Select Wan Chai" not in prompt
                return gateway_reply(_decision(prompt, "Click Show offers"))
            if len(requests) == 3:
                return gateway_reply(_decision(prompt, "Select Wan Chai in Branch"))
            if len(requests) == 4:
                return gateway_reply(_decision(prompt, "Click Check stock"))
            assert "EX4 at Wan Chai: HKD 399. Stock not published." in prompt
            if len(requests) == 5:
                return gateway_reply("5-finish")
            assert len(requests) == 6
            return gateway_reply(json.dumps({
                "found": True,
                "answer": "EX4 is HKD 399 at Wan Chai. Stock is unpublished and unconfirmed.",
            }))

        async with AsyncOpenAI(base_url="http://gateway.invalid/v1", api_key="local",
                               http_client=httpx.AsyncClient(
                                   transport=httpx.MockTransport(script),
                               )) as client:
            browser = WebBrowseRunner(client=client, model=ALIAS, max_tokens=512,
                                      settings=BrowseSettings(), manager=manager)
            admission = admission_for_tests(tmp_path)
            registry = registry_for(browser, manager, admission)
            bindings = bindings_for(tmp_path)
            result = await _dispatch(registry, admission, bindings, UNIT, {
                "objective": "Check the offer and published stock for Wan Chai.",
                "start_url": url,
            })
            assert result["status"] == "ok", result
            assert result["url"] == url
            assert result["answer"] == (
                "EX4 is HKD 399 at Wan Chai. Stock is unpublished and unconfirmed."
            )
            [tab] = await tabs.open_for_scope(scope, instance="generated-instance")
            assert result["task"] == str(tab.task_id) and result["tab"] == tab.target_id
            assert len(requests) == 6 and commits == []

            other_unit = "generated-other-unit"
            other_scope = computer_unit_scope(other_unit)
            foreign_registry = registry_for(browser, manager, admission, unit=other_unit)
            foreign_bindings = {**bindings, "conversation_id": "generated-other-room"}
            refused = await _dispatch(foreign_registry, admission, foreign_bindings, other_unit, {
                "objective": "Check whether the offer is still shown.",
                "start_url": url + "?foreign=1",
                "task": result["task"],
            })
            assert refused["status"] == "unavailable" and refused["reason"] == "expired", refused
            assert connections == [scope] and visits == ["/offer"] and len(requests) == 6
            assert await tabs.open_for_scope(other_scope, instance="generated-instance") == []
            assert await tabs.open_for_scope(scope, instance="generated-instance") == [tab]
