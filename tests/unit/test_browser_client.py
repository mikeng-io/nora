"""The CDP client for a room's browser (`nora.infra.computer.browser`).

A fake CDP connection answers the calls `BrowserPage` makes and records them, so
each test reads what the client asked Chrome to do. The refusal path runs
against a real local server that counts the requests it receives.

Pinned: page reads run in an isolated world, never the page's own; navigation
is told by the frame's loaderId; a refusal is not asked again (a second request
could start a browser); a password is masked by its input type, not its label;
a new tab is closed and followed; marks are drawn on the image, not the page;
the sub-agent runs one tool at a time.
"""

from __future__ import annotations

import base64
import io
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from PIL import Image

from nora.agent.factory import RetryPolicy, create_browse_agent
from nora.infra.computer.browser import (
    ActionRefusedError,
    BrowserError,
    BrowserPage,
    CdpConnection,
    RefError,
    draw_marks,
    open_browser,
)


def _jpeg(color: tuple[int, int, int] = (255, 255, 255),
          size: tuple[int, int] = (200, 100)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format="JPEG")
    return out.getvalue()


class FakeCdp:
    """Answers the calls `BrowserPage` makes; `calls` records (method, params)."""

    def __init__(self, *, nodes: list[dict[str, Any]] | None = None,
                 password_nodes: frozenset[int] = frozenset(),
                 targets: list[list[dict[str, Any]]] | None = None,
                 submit_click: bool = False, focus_in_form: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.nodes = nodes or []
        self.password_nodes = password_nodes
        # Whether the element a click or a key names is a form's submit control, and
        # whether the focused element sits in a form (Enter there submits it).
        self.submit_click = submit_click
        self.focus_in_form = focus_in_form
        self.loader = "L1"
        self.loaders_after_input: list[str] = []
        self.target_lists = targets or [[{"targetId": "T0", "type": "page", "url": "about:blank"}]]

    async def call(self, method: str, params: dict[str, Any] | None = None, *,
                   session_id: str | None = None, wait_s: float = 30.0) -> dict[str, Any]:
        params = params or {}
        self.calls.append((method, params))
        if method == "Page.getFrameTree":
            return {"frameTree": {"frame": {"id": "F", "loaderId": self.loader}}}
        if method == "Page.createIsolatedWorld":
            return {"executionContextId": 42}
        if method == "Runtime.evaluate":
            expression = str(params.get("expression"))
            if "activeElement" in expression:
                return {"result": {"value": self.focus_in_form}}
            values = {"location.href": "https://example.com", "document.title": "Example",
                      "document.readyState": "complete"}
            return {"result": {"value": values.get(expression, "")}}
        if method == "Accessibility.getFullAXTree":
            return {"nodes": self.nodes}
        if method == "DOM.describeNode":
            kind = "password" if params["backendNodeId"] in self.password_nodes else "text"
            return {"node": {"attributes": ["type", kind]}}
        if method == "DOM.resolveNode":
            return {"object": {"objectId": "obj"}}
        if method == "Runtime.callFunctionOn":
            if "form" in str(params.get("functionDeclaration")):
                return {"result": {"value": self.submit_click}}
            if self.loaders_after_input:
                self.loader = self.loaders_after_input.pop(0)
            return {"result": {"value": "Newest"}}
        if method == "DOM.getBoxModel":
            return {"model": {"content": [10, 10, 50, 10, 50, 30, 10, 30],
                              "border": [10, 10, 50, 10, 50, 30, 10, 30]}}
        if method == "Page.getLayoutMetrics":
            return {"cssVisualViewport": {"clientWidth": 1280, "clientHeight": 800}}
        if method == "Input.dispatchMouseEvent" and params.get("type") == "mouseReleased":
            if self.loaders_after_input:
                self.loader = self.loaders_after_input.pop(0)
            return {}
        if method == "Target.getTargets":
            lists = self.target_lists
            current = lists[0] if len(lists) == 1 else lists.pop(0)
            return {"targetInfos": current}
        if method == "Page.navigate":
            return {"frameId": "F", "loaderId": "L-nav"}
        if method == "Page.captureScreenshot":
            return {"data": base64.b64encode(_jpeg()).decode()}
        return {}

    def methods(self) -> list[str]:
        return [m for m, _ in self.calls]


def _node(role: str, name: str, backend: int, value: str = "") -> dict[str, Any]:
    return {"role": {"value": role}, "name": {"value": name}, "backendDOMNodeId": backend,
            "value": {"value": value}, "properties": []}


def _page(cdp: FakeCdp) -> BrowserPage:
    return BrowserPage(cdp=cdp, session_id="S", target_id="T0")  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    """`settle`'s waits shortened, so a test does not sleep."""
    monkeypatch.setattr("nora.infra.computer.browser.LOAD_SECONDS", 0.5)

    async def no_sleep(_: float) -> None:
        return None
    monkeypatch.setattr("nora.infra.computer.browser.asyncio.sleep", no_sleep)


# -- the action policy at the page boundary ----------------------------------------


def _dispatched(cdp: FakeCdp) -> list[str]:
    """The input and insertion calls that reached the page."""
    return [m for m in cdp.methods() if m.startswith("Input.")]


async def test_a_click_on_a_submit_control_is_refused_and_nothing_reaches_the_page():
    """Aikido 8765_44853: a click that submits a form is a write, and the read-only browse
    contract refuses it without a person's approval. The refusal is checked before any
    mouse event is sent, so the page sees nothing."""
    cdp = FakeCdp(nodes=[_node("button", "Send", 3)], submit_click=True)
    page = _page(cdp)
    await page.snapshot()
    with pytest.raises(ActionRefusedError, match="submit"):
        await page.click("e1")
    assert _dispatched(cdp) == []


async def test_a_plain_click_still_reaches_the_page():
    cdp = FakeCdp(nodes=[_node("link", "More", 3)])
    page = _page(cdp)
    await page.snapshot()
    await page.click("e1")
    assert "Input.dispatchMouseEvent" in cdp.methods()


async def test_enter_in_a_form_is_refused_and_outside_a_form_it_is_not():
    cdp = FakeCdp(focus_in_form=True)
    with pytest.raises(ActionRefusedError, match="submit"):
        await _page(cdp).press("Enter")
    assert _dispatched(cdp) == []
    free = FakeCdp(focus_in_form=False)
    await _page(free).press("Enter")
    assert "Input.dispatchKeyEvent" in free.methods()


async def test_typing_into_a_password_field_is_refused_and_nothing_is_inserted():
    cdp = FakeCdp(nodes=[_node("textbox", "Pass", 4)], password_nodes=frozenset({4}))
    page = _page(cdp)
    await page.snapshot()
    with pytest.raises(ActionRefusedError, match="sensitive"):
        await page.type_text("e1", "hunter2")
    assert "Input.insertText" not in cdp.methods()


async def test_typing_with_submit_into_a_form_is_refused_before_the_text_goes_in():
    cdp = FakeCdp(nodes=[_node("textbox", "Search", 4)], submit_click=True)
    page = _page(cdp)
    await page.snapshot()
    with pytest.raises(ActionRefusedError, match="submit"):
        await page.type_text("e1", "cats", submit=True)
    assert "Input.insertText" not in cdp.methods()


async def test_a_revocation_during_a_click_stops_the_mouse_events():
    """The policy is checked again immediately before the input is sent: a revocation that
    lands while the click is being prepared (here, on its box lookup) sends no mouse event."""

    class RevokeOnBox(FakeCdp):
        page: BrowserPage | None = None

        async def call(self, method: str, params: dict[str, Any] | None = None, **kwargs: Any):
            if method == "DOM.getBoxModel" and self.page is not None:
                self.page.revoke()
            return await super().call(method, params, **kwargs)

    cdp = RevokeOnBox(nodes=[_node("link", "More", 3)])
    page = _page(cdp)
    cdp.page = page
    await page.snapshot()
    with pytest.raises(ActionRefusedError):
        await page.click("e1")
    assert "Input.dispatchMouseEvent" not in cdp.methods()


async def test_a_revoked_page_dispatches_nothing_at_all():
    """`browse_needs_human` and `browse_done` revoke the page: even an action that would be
    allowed is refused, so no later call can act on the page."""
    cdp = FakeCdp(nodes=[_node("link", "More", 3)])
    page = _page(cdp)
    await page.snapshot()
    page.revoke()
    with pytest.raises(ActionRefusedError):
        await page.click("e1")
    with pytest.raises(ActionRefusedError):
        await page.navigate("https://example.com/next")
    with pytest.raises(ActionRefusedError):
        await page.press("Tab")
    assert _dispatched(cdp) == [] and "Page.navigate" not in cdp.methods()


# -- reads run in an isolated world -------------------------------------------


async def test_every_page_read_runs_in_the_isolated_world_never_the_pages_own():
    cdp = FakeCdp(nodes=[_node("button", "Go", 1)])
    page = _page(cdp)
    await page.snapshot()
    evaluates = [p for m, p in cdp.calls if m == "Runtime.evaluate"]
    assert evaluates and all(p.get("contextId") == 42 for p in evaluates)
    assert "Runtime.enable" not in cdp.methods()


async def test_an_element_call_resolves_into_the_isolated_world():
    cdp = FakeCdp(nodes=[_node("combobox", "Sort", 5)])
    page = _page(cdp)
    await page.snapshot()
    await page.select("e1", "Newest")
    resolved = [p for m, p in cdp.calls if m == "DOM.resolveNode"]
    assert resolved and resolved[0]["executionContextId"] == 42


# -- navigation by loaderId ----------------------------------------------------


async def test_a_select_that_reloads_the_page_is_waited_for():
    """`select` reads the loader BEFORE acting, so a reload it triggers is
    seen: the readiness poll comes after the loader moved."""
    cdp = FakeCdp(nodes=[_node("combobox", "Sort", 5)])
    cdp.loaders_after_input = ["L2"]
    page = _page(cdp)
    await page.snapshot()
    cdp.calls.clear()
    await page.select("e1", "Newest")
    methods = cdp.methods()
    assert methods.index("Page.getFrameTree") < methods.index("Runtime.callFunctionOn")


async def test_a_same_document_navigation_does_not_wait_for_a_new_document(monkeypatch):
    cdp = FakeCdp()

    async def same_doc(method, params=None, **kw):
        if method == "Page.navigate":
            cdp.calls.append((method, params or {}))
            return {"frameId": "F"}  # no loaderId: a #fragment
        return await FakeCdp.call(cdp, method, params, **kw)
    monkeypatch.setattr(cdp, "call", same_doc)
    page = _page(cdp)
    await page.navigate("https://example.com/#part")
    frame_reads_after = cdp.methods()[cdp.methods().index("Page.navigate"):].count(
        "Page.getFrameTree")
    assert frame_reads_after <= 3  # no polling for a loader that never moves


# -- refs, passwords, order -----------------------------------------------------


async def test_a_password_is_masked_by_its_input_type_whatever_its_label_says():
    nodes = [_node("textbox", "密碼", 1, value="hunter2"),
             _node("textbox", "Search", 2, value="harbour")]
    page = _page(FakeCdp(nodes=nodes, password_nodes=frozenset({1})))
    snap = await page.snapshot()
    assert [e.value for e in snap.elements] == ["•••", "harbour"]
    assert "hunter2" not in snap.render()


async def test_controls_come_before_links_so_a_long_page_keeps_its_search_box(monkeypatch):
    monkeypatch.setattr("nora.infra.computer.browser.MAX_SNAPSHOT_ELEMENTS", 3)
    nodes = [_node("link", f"link {i}", i) for i in range(10)] + [_node("searchbox", "Q", 100)]
    snap = await _page(FakeCdp(nodes=nodes)).snapshot()
    assert snap.elements[0].role == "searchbox" and snap.more == 8


async def test_a_stale_ref_is_refused():
    page = _page(FakeCdp(nodes=[_node("button", "Go", 7)]))
    await page.snapshot()
    with pytest.raises(RefError):
        await page.click("e2")


# -- clicks and tabs ----------------------------------------------------------


async def test_a_new_tab_a_click_opens_is_closed_and_followed_here():
    ours = {"targetId": "T0", "type": "page", "url": "https://example.com"}
    popup = {"targetId": "T9", "type": "page", "url": "https://example.com/opened"}
    cdp = FakeCdp(nodes=[_node("link", "Open", 3)], targets=[[ours], [ours, popup], [ours]])
    page = _page(cdp)
    await page.snapshot()
    await page.click("e1")
    assert ("Target.closeTarget", {"targetId": "T9"}) in cdp.calls
    assert ("Page.navigate", {"url": "https://example.com/opened"}) in cdp.calls


async def test_a_click_lands_inside_the_window_even_for_a_tall_element():
    cdp = FakeCdp(nodes=[_node("button", "Tall", 4)])

    async def tall(method, params=None, **kw):
        if method == "DOM.getBoxModel":
            cdp.calls.append((method, params or {}))
            return {"model": {"content": [0, 0, 100, 0, 100, 3000, 0, 3000]}}
        return await FakeCdp.call(cdp, method, params, **kw)
    cdp.call = tall  # type: ignore[method-assign]
    page = _page(cdp)
    await page.snapshot()
    await page.click("e1")
    presses = [p for m, p in cdp.calls if m == "Input.dispatchMouseEvent"]
    assert presses and all(0 < p["y"] < 800 for p in presses)


# -- marks on the image ---------------------------------------------------------


async def test_marks_are_drawn_on_the_image_and_nothing_is_put_into_the_page():
    cdp = FakeCdp(nodes=[_node("button", "Go", 1)])
    page = _page(cdp)
    await page.snapshot()
    cdp.calls.clear()
    marked = await page.screenshot(marks=True)
    assert "Runtime.evaluate" not in cdp.methods()
    assert "Runtime.callFunctionOn" not in cdp.methods()
    image = Image.open(io.BytesIO(marked)).convert("RGB")
    r, g, b = image.getpixel((10, 20))  # on the box's left edge
    assert r > 150 and g < 120 and b < 150


def test_draw_marks_skips_a_box_off_the_image():
    plain = _jpeg()
    out = draw_marks(plain, [("e1", 500, 500, 600, 600)])
    a = Image.open(io.BytesIO(plain)).convert("RGB")
    b = Image.open(io.BytesIO(out)).convert("RGB")
    assert a.getpixel((5, 5)) == b.getpixel((5, 5))


# -- the connection -------------------------------------------------------------


async def test_a_refusal_is_not_asked_again():
    """computerd answers a refusal as JSON instead of upgrading. The client
    must not GET the address again to learn why: that request runs computerd's
    start path a second time (it can start Chrome, or evict a room's browser)."""
    hits = 0

    async def refuse(request: web.Request) -> web.Response:
        nonlocal hits
        hits += 1
        return web.json_response({"status": "denied", "reason": "slots full"})

    app = web.Application()
    app.router.add_get("/v1/browser", refuse)
    server = TestServer(app)
    await server.start_server()
    try:
        with pytest.raises(BrowserError):
            async with open_browser(base_url=str(server.make_url("")), token="t",  # noqa: S106
                                    scope="ab" * 8):
                pass
    finally:
        await server.close()
    assert hits == 1


async def test_a_cdp_error_answer_raises_and_close_fails_what_is_pending():
    sent: list[str] = []

    class FakeWs:
        closed = False

        def __init__(self) -> None:
            self.queue: list[Any] = []

        async def send_str(self, data: str) -> None:
            sent.append(data)

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

        async def close(self) -> None:
            self.closed = True

    conn = CdpConnection(FakeWs())  # type: ignore[arg-type]
    with pytest.raises(BrowserError):
        await conn.call("Page.navigate", {"url": "x"}, wait_s=0.5)
    await conn.close()


def test_the_sub_agent_runs_one_tool_at_a_time():
    from strands.tools.executors import SequentialToolExecutor

    from nora.agent import factory
    client = factory.create_client(base_url="http://localhost:1/v1", api_key="k")
    model = factory.create_model_provider(model="m", max_tokens=10, temperature=0, client=client)
    agent = create_browse_agent(model=model, system_prompt="p", tools=[], trace_attributes={},
                                retry=RetryPolicy(max_attempts=1, initial_delay=1, max_delay=1))
    assert isinstance(agent.tool_executor, SequentialToolExecutor)
