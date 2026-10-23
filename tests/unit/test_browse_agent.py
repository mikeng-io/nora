"""The browse sub-agent's own parts, without a model: the page tools it is given,
its step budget, how its two finishing tools set the outcome, and how the runner
turns failures into outcomes. The CDP client is covered by
tests/unit/test_browser_client.py."""

from __future__ import annotations

import asyncio
from typing import Any

from nora.agent import browse
from nora.agent.browse import BrowseAgentRunner, BrowseSettings, _Task, _tools
from nora.infra.computer.browser import BrowserError, RefError, Snapshot


class FakePage:
    """The `BrowserPage` surface the tools use, recording each action."""

    def __init__(self) -> None:
        self.actions: list[tuple[str, Any]] = []
        self.revoked = False

    def revoke(self) -> None:
        self.revoked = True

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
    task, page = _task()
    await _named(task)["browse_needs_human"](reason="a CAPTCHA")
    assert page.revoked
    assert task.finished is not None
    assert task.finished.status == "unavailable" and task.finished.needs_human
    assert task.finished.reason == "a CAPTCHA"


async def test_once_browse_done_no_page_tool_touches_the_page_again():
    """Aikido 8765_44870: a finished task is over. Every page tool must refuse, and the page
    must see no further action, even though the model keeps calling tools."""
    task, page = _task()
    tools = _named(task)
    await tools["browse_done"](answer="Open 9 to 5.")
    before = list(page.actions)
    for name, args in [("page_open", {"url": "https://example.com"}), ("page_snapshot", {}),
                       ("page_click", {"ref": "e1"}), ("page_read", {}), ("page_look", {})]:
        said = await tools[name](**args)
        text = said if isinstance(said, str) else said["content"][0]["text"]
        assert "finished" in text, (name, text)
    assert page.actions == before and page.revoked
    assert task.finished is not None and task.finished.answer == "Open 9 to 5."


async def test_a_second_terminal_outcome_is_rejected_and_the_first_stands():
    """Aikido 8765_44870: `browse_done` and `browse_needs_human` are one-way. A second
    terminal call must not overwrite the first outcome."""
    task, _ = _task()
    tools = _named(task)
    await tools["browse_needs_human"](reason="a CAPTCHA")
    said = await tools["browse_done"](answer="a later answer")
    assert "already" in said
    assert task.finished is not None
    assert task.finished.needs_human and task.finished.reason == "a CAPTCHA"
    assert task.finished.answer == ""
    task2, _ = _task()
    tools2 = _named(task2)
    await tools2["browse_done"](answer="first")
    await tools2["browse_needs_human"](reason="late")
    assert task2.finished is not None and task2.finished.answer == "first"
    assert not task2.finished.needs_human


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
