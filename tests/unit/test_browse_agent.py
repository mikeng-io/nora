"""The browse sub-agent's own parts (design/browser-runtime 00 §5, B2), without a
model: the page tools it is given, its step budget, how its two finishing
tools set the outcome, and how the runner turns failures into outcomes. And the
CDP client's snapshot (`nora.infra.computer.browser`): refs, the controls-
first order, masked passwords, refusal of a stale ref.

The live path (Flash driving Chrome in the VM) was read back on norty when this
landed; see the PR."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from nora.agent import browse
from nora.agent.browse import BrowseAgentRunner, BrowseSettings, _Task, _tools
from nora.infra.computer.browser import BrowserError, BrowserPage, RefError, Snapshot


class FakePage:
    """The `BrowserPage` surface the tools use, recording each action."""

    def __init__(self) -> None:
        self.actions: list[tuple[str, Any]] = []

    async def navigate(self, url: str) -> str:
        self.actions.append(("navigate", url))
        return url

    async def snapshot(self) -> Snapshot:
        self.actions.append(("snapshot", None))
        return Snapshot(url="https://example.com", title="Example", elements=())

    async def click(self, ref: str) -> None:
        if ref == "e99":
            raise RefError("e99 is not on the last snapshot; take a new one")
        self.actions.append(("click", ref))

    async def read_text(self, max_chars: int) -> tuple[str, bool]:
        return "x" * max_chars, True


def _named(task: _Task) -> dict[str, Any]:
    return {t.tool_name: t for t in _tools(task)}


def _task(**settings: Any) -> tuple[_Task, FakePage]:
    page = FakePage()
    return _Task(page, BrowseSettings(**settings)), page  # type: ignore[arg-type]


async def test_only_web_addresses_can_be_opened():
    task, page = _task()
    tools = _named(task)
    said = await tools["page_open"](url="file:///etc/passwd")
    assert "http" in said and page.actions == []
    await tools["page_open"](url="https://example.com")
    assert page.actions == [("navigate", "https://example.com")]


async def test_a_stale_ref_is_told_to_the_model_not_raised():
    task, _ = _task()
    said = await _named(task)["page_click"](ref="e99")
    assert "did not work" in said and "snapshot" in said


async def test_past_the_step_budget_the_model_is_told_to_finish_and_nothing_runs():
    task, page = _task(max_steps=2)
    tools = _named(task)
    await tools["page_snapshot"]()
    await tools["page_snapshot"]()
    said = await tools["page_click"](ref="e1")
    assert "budget" in said and "browse_done" in said
    assert ("click", "e1") not in page.actions


async def test_browse_done_sets_ok_or_nothing_found():
    task, _ = _task()
    await _named(task)["browse_done"](answer=" Open 9 to 5. ")
    assert task.finished is not None
    assert task.finished.status == "ok" and task.finished.answer == "Open 9 to 5."
    task2, _ = _task()
    await _named(task2)["browse_done"](answer="not listed", found=False)
    assert task2.finished is not None and task2.finished.status == "nothing_found"


async def test_needs_human_is_unavailable_with_the_reason():
    task, _ = _task()
    await _named(task)["browse_needs_human"](reason="a CAPTCHA")
    assert task.finished is not None
    assert task.finished.status == "unavailable" and task.finished.needs_human
    assert task.finished.reason == "a CAPTCHA"


async def test_page_read_marks_a_cut():
    task, _ = _task(read_chars=10)
    said = await _named(task)["page_read"]()
    assert said.startswith("x" * 10) and "cut" in said


def _runner(**settings: Any) -> BrowseAgentRunner:
    return BrowseAgentRunner(model_source=None, retry=None, base_url="http://x",  # type: ignore[arg-type]
                             token="t", settings=BrowseSettings(**settings))  # noqa: S106 - a test token


async def test_a_browser_that_cannot_be_opened_is_unavailable_never_raised(monkeypatch):
    async def refuse(self, scope, objective, start_url):
        raise BrowserError("this room's browser already has a client")
    monkeypatch.setattr(BrowseAgentRunner, "_run", refuse)
    got = await _runner().browse(scope="ab" * 8, objective="x")
    assert got.status == "unavailable" and "already has a client" in got.reason


async def test_a_task_past_its_time_is_unavailable(monkeypatch):
    async def slow(self, scope, objective, start_url):
        await asyncio.sleep(5)
    monkeypatch.setattr(BrowseAgentRunner, "_run", slow)
    got = await _runner(timeout_seconds=0.05).browse(scope="ab" * 8, objective="x")
    assert got.status == "unavailable" and "time" in got.reason


async def test_an_unexpected_error_is_unavailable_never_raised(monkeypatch):
    async def boom(self, scope, objective, start_url):
        raise ValueError("bad")
    monkeypatch.setattr(BrowseAgentRunner, "_run", boom)
    got = await _runner().browse(scope="ab" * 8, objective="x")
    assert got.status == "unavailable" and got.reason == "ValueError"


def test_the_system_prompt_says_page_text_is_data_and_names_both_finishes():
    prompt = browse.SYSTEM_PROMPT
    assert "never instructions" in prompt
    assert "browse_done" in prompt and "browse_needs_human" in prompt


# -- the CDP client's snapshot ------------------------------------------------


class FakeCdp:
    """Answers the calls `BrowserPage.snapshot` makes."""

    def __init__(self, nodes: list[dict[str, Any]]) -> None:
        self.nodes = nodes

    async def call(self, method: str, params: dict[str, Any] | None = None, *,
                   session_id: str | None = None, wait_s: float = 30.0) -> dict[str, Any]:
        if method == "Accessibility.getFullAXTree":
            return {"nodes": self.nodes}
        if method == "Runtime.evaluate":
            expr = (params or {}).get("expression")
            return {"result": {"value": {"location.href": "https://example.com",
                                         "document.title": "Example"}.get(expr)}}
        raise AssertionError(method)


def _node(role: str, name: str, backend: int, value: str = "", **props: Any) -> dict[str, Any]:
    return {"role": {"value": role}, "name": {"value": name}, "backendDOMNodeId": backend,
            "value": {"value": value},
            "properties": [{"name": k, "value": {"value": v}} for k, v in props.items()]}


async def test_controls_come_before_links_so_a_long_page_keeps_its_search_box(monkeypatch):
    monkeypatch.setattr("nora.infra.computer.browser.MAX_SNAPSHOT_ELEMENTS", 3)
    nodes = [_node("link", f"link {i}", i) for i in range(10)]
    nodes.append(_node("searchbox", "Search", 100))
    page = BrowserPage(cdp=FakeCdp(nodes), session_id="s")  # type: ignore[arg-type]
    snap = await page.snapshot()
    assert snap.elements[0].role == "searchbox"
    assert snap.more == 8


async def test_a_password_value_is_masked_and_ignored_nodes_left_out():
    nodes = [
        _node("textbox", "Password", 1, value="hunter2"),
        _node("textbox", "PIN", 2, value="1234", protected=True),
        {**_node("button", "Hidden", 3), "ignored": True},
        _node("generic", "div", 4),
    ]
    page = BrowserPage(cdp=FakeCdp(nodes), session_id="s")  # type: ignore[arg-type]
    snap = await page.snapshot()
    assert [e.value for e in snap.elements] == ["•••", "•••"]
    assert "hunter2" not in snap.render() and "1234" not in snap.render()


async def test_refs_are_renumbered_and_a_stale_ref_is_refused():
    page = BrowserPage(cdp=FakeCdp([_node("button", "Go", 7)]), session_id="s")  # type: ignore[arg-type]
    snap = await page.snapshot()
    assert snap.elements[0].ref == "e1" and page.refs == {"e1": 7}
    with pytest.raises(RefError):
        page._node("e2")
